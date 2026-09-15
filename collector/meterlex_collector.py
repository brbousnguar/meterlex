#!/usr/bin/env python3
"""Meterlex collector: reads the AI coding tools' session logs on this machine
and sends their token counts to the Meterlex hub.

Only counts leave the machine: tool, model, time, session id, project label and
token numbers. Prompts, replies, tool output and file contents are never read
into what is sent. `run --dry-run` prints exactly what would go.

    python meterlex_collector.py setup --hub URL --key KEY [--machine NAME] [--labels full|basename|hash]
    python meterlex_collector.py run [--dry-run] [--full]
    python meterlex_collector.py loop [--every 300]
    python meterlex_collector.py status
    python meterlex_collector.py install      # launchd on macOS, a Scheduled Task on Windows

One file, standard library only, Python 3.9+: macOS's own /usr/bin/python3 runs
it, and so does `uv run meterlex_collector.py …` on Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

VERSION = "0.1.0"
BATCH = 1000               # turns per POST
SPOOL_MAX = 500_000        # unsent turns kept on disk before the oldest are dropped
HOME = Path.home()

DEFAULT_PATHS = {
    "claude_code": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "antigravity": "~/.gemini/antigravity-cli",
    "gemini_cli": "~/.gemini",
    "copilot": "~/.copilot",
}
LABELS = ("full", "basename", "hash")
FREE_CODEX_PROVIDERS = {"ollama-launch", "ollama-launch-codex-app", "ollama"}


def config_dir() -> Path:
    if os.environ.get("METERLEX_HOME"):
        return Path(os.environ["METERLEX_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(HOME / "AppData" / "Roaming"))) / "meterlex"
    return HOME / ".config" / "meterlex"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = _read_json(config_dir() / "config.json", {})
    cfg.setdefault("labels", "full")
    cfg.setdefault("paths", {})
    cfg.setdefault("machine", platform.node().split(".")[0].lower())
    if os.environ.get("METERLEX_HUB"):
        cfg["hub"] = os.environ["METERLEX_HUB"]
    if os.environ.get("METERLEX_KEY"):
        cfg["key"] = os.environ["METERLEX_KEY"]
    return cfg


def source_path(cfg: dict, name: str) -> Path:
    return Path(os.path.expanduser(cfg["paths"].get(name) or DEFAULT_PATHS[name]))


# ── shared helpers ────────────────────────────────────────────────────────────

def _parse_ts(ts) -> Optional[str]:
    """ISO timestamp → naive UTC ISO string, the hub's storage format."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _ms_ts(ms) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read", "cache_write", "reasoning_tokens")


def turn(source, session_id, turn_key, project, model_id, ts, inp=0, out=0, cache_read=0,
         cache_write=0, reasoning=0, total=None, origin=None, snapshot=False, alt_keys=None) -> dict:
    """One usage record, the hub's wire format. `snapshot` rows (a whole
    session's running totals) replace their previous values on the hub.
    `alt_keys` are older keys the same usage was once stored under, so the hub
    can fold those rows into this one."""
    t = {
        "source": source, "session_id": str(session_id), "turn_key": str(turn_key),
        "project": project or "", "model_id": model_id or "unknown", "ts": ts or _now(),
        "input_tokens": inp, "output_tokens": out, "cache_read": cache_read, "cache_write": cache_write,
        "reasoning_tokens": reasoning,
        "total_tokens": total if total is not None else inp + out + cache_read + cache_write,
        "origin": origin, "snapshot": snapshot,
    }
    if alt_keys:
        t["alt_keys"] = list(alt_keys)
    return t


def _merge_max(a: dict, b: dict) -> dict:
    """Two records of the same reply: each token count at its largest (a reply's
    numbers only grow as it streams), the earliest time, every old key."""
    m = dict(a)
    for f in TOKEN_FIELDS:
        m[f] = max(a[f], b[f])
    m["total_tokens"] = m["input_tokens"] + m["output_tokens"] + m["cache_read"] + m["cache_write"]
    m["ts"] = min(a["ts"], b["ts"])
    m["origin"] = a.get("origin") or b.get("origin")
    keys = list(dict.fromkeys((a.get("alt_keys") or []) + (b.get("alt_keys") or [])))
    if keys:
        m["alt_keys"] = keys
    return m


def _lines(fp: Path, offset: int) -> Iterator[tuple[int, Optional[dict]]]:
    """Complete JSONL lines from `offset`, as (end offset, parsed or None). A
    trailing line without its newline is left for the next pass."""
    with open(fp, "rb") as fh:
        fh.seek(offset)
        while True:
            raw = fh.readline()
            if not raw or not raw.endswith((b"\n", b"\r")):
                return
            offset += len(raw)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                yield offset, json.loads(line)
            except ValueError:
                yield offset, None


def _incremental(state: dict, key: str, fp: Path, full: bool) -> Optional[int]:
    """Where to resume reading `fp`, or None when it hasn't changed."""
    try:
        size = fp.stat().st_size
    except OSError:
        return None
    seen = state["files"].get(key)
    if full or not seen or size < seen.get("size", 0):
        return 0
    if size == seen.get("size"):
        return None
    return seen.get("offset", 0)


def _mark(state: dict, key: str, fp: Path, offset: int, **extra) -> None:
    try:
        size = fp.stat().st_size
    except OSError:
        size = offset
    state["files"][key] = {"offset": offset, "size": size, **extra}


def _snapshot_changed(state: dict, key: str, t: dict) -> bool:
    digest = hashlib.sha1(json.dumps(t, sort_keys=True).encode()).hexdigest()
    if state["snapshots"].get(key) == digest:
        return False
    state["snapshots"][key] = digest
    return True


# ── Claude Code: ~/.claude/projects/**/*.jsonl ────────────────────────────────

def _claude_origin(event: dict) -> Optional[str]:
    if event.get("isSidechain"):
        return "subagent"
    entry = event.get("entrypoint") or ""
    if entry == "cli":
        return "interactive"
    if entry.startswith("sdk"):
        return "automated"
    return None


def read_claude_code(root: Path, state: dict, full: bool) -> Iterator[dict]:
    """One record per reply. Claude Code logs each part of a reply (thinking,
    text, each tool call) as its own line carrying the whole reply's usage, so
    counting lines counts a reply two or three times; the reply's message id is
    the key, and its per-line uuids travel as alt_keys."""
    for fp in sorted(root.rglob("*.jsonl")):
        key = "cc:" + fp.relative_to(root).as_posix()
        offset = _incremental(state, key, fp, full)
        if offset is None:
            continue
        replies: dict = {}
        end = offset
        for end, event in _lines(fp, offset):
            if not event or event.get("type") != "assistant":
                continue
            msg = event.get("message") or {}
            usage = msg.get("usage")
            uuid = event.get("uuid")
            if not usage or not uuid:
                continue
            msg_id = msg.get("id")
            t = turn(
                "claude-code", event.get("sessionId") or fp.stem, msg_id or uuid, event.get("cwd", ""),
                msg.get("model", "unknown"), _parse_ts(event.get("timestamp")),
                inp=_to_int(usage.get("input_tokens")), out=_to_int(usage.get("output_tokens")),
                cache_read=_to_int(usage.get("cache_read_input_tokens")),
                cache_write=_to_int(usage.get("cache_creation_input_tokens")),
                origin=_claude_origin(event), alt_keys=[uuid] if msg_id else None,
            )
            k = (t["session_id"], t["turn_key"])
            replies[k] = _merge_max(replies[k], t) if k in replies else t
        yield from replies.values()
        _mark(state, key, fp, end)


# ── Codex: ~/.codex/sessions/**/*.jsonl (token_count events) ──────────────────

def _codex_meta(fp: Path) -> tuple[str, str, str, str]:
    """(session_id, provider, cwd, originator) from the first session_meta."""
    try:
        with open(fp, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "session_meta":
                    p = e.get("payload") or {}
                    return (p.get("session_id") or p.get("id") or fp.stem, p.get("model_provider", "openai"),
                            p.get("cwd", ""), p.get("originator", ""))
    except OSError:
        pass
    return fp.stem, "openai", "", ""


def read_codex(root: Path, state: dict, full: bool) -> Iterator[dict]:
    for fp in sorted(root.rglob("*.jsonl")):
        key = "cx:" + fp.relative_to(root).as_posix()
        offset = _incremental(state, key, fp, full)
        if offset is None:
            continue
        session_id, provider, project, originator = _codex_meta(fp)
        is_free = provider in FREE_CODEX_PROVIDERS
        fallback = f"local/{provider}" if is_free else "gpt-5"
        # the model in effect where the last pass stopped (turn_context events
        # before `offset` aren't seen again)
        current = (state["files"].get(key) or {}).get("last_model") if offset else None
        current = current or fallback
        origin = "automated" if "exec" in originator else ("interactive" if originator else None)
        # Codex sometimes repeats a token_count event with an unchanged running
        # total; its last_token_usage would then count the same turn twice.
        last_total = (state["files"].get(key) or {}).get("last_total") if offset else None
        end = offset
        for end, event in _lines(fp, offset):
            if not event:
                continue
            if event.get("type") == "turn_context":
                current = (event.get("payload") or {}).get("model") or current
                continue
            payload = event.get("payload") or {}
            if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            last = info.get("last_token_usage") or {}
            if not isinstance(last, dict) or not last:
                continue
            running = info.get("total_token_usage")
            if running is not None and running == last_total:
                continue
            last_total = running
            ts_str = event.get("timestamp", "")
            inp, out = _to_int(last.get("input_tokens")), _to_int(last.get("output_tokens"))
            yield turn(
                "codex", session_id, ts_str or f"offset:{end}", project, fallback if is_free else current,
                _parse_ts(ts_str), inp=inp, out=out, cache_read=_to_int(last.get("cached_input_tokens")),
                reasoning=_to_int(last.get("reasoning_output_tokens")), total=inp + out, origin=origin,
            )
        _mark(state, key, fp, end, last_model=current, last_total=last_total)


# ── Antigravity: ~/.gemini/antigravity-cli/conversations/*.db ─────────────────

_AGY_MODEL = re.compile(rb"gemini[a-z0-9\-\.]+", re.I)
_AGY_WS = re.compile(rb"file:///[^\x00-\x08\x0a-\x1f]+")


def _pv(data: bytes, pos: int) -> tuple[int, int]:
    r = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        r |= (b & 0x7F) << shift
        if not b & 0x80:
            return r, pos
        shift += 7
    return r, pos


def _pf(data: bytes) -> dict:
    """Shallow protobuf parse → {field: [values]}."""
    out: dict = {}
    pos, n = 0, len(data)
    while pos < n:
        try:
            tag, pos = _pv(data, pos)
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                v, pos = _pv(data, pos)
                out.setdefault(fn, []).append(v)
            elif wt == 2:
                ln, pos = _pv(data, pos)
                out.setdefault(fn, []).append(data[pos:pos + ln])
                pos += ln
            elif wt == 1:
                pos += 8
            elif wt == 5:
                pos += 4
            else:
                break
        except Exception:
            break
    return out


def _agy_usage(payload: bytes) -> tuple[int, int]:
    """(output tokens, cumulative prompt tokens): payload → 5 → 9 → {3, 5}."""
    try:
        for f5 in _pf(payload).get(5, []):
            if isinstance(f5, bytes):
                for f9 in _pf(f5).get(9, []):
                    if isinstance(f9, bytes):
                        p = _pf(f9)
                        return (next((v for v in p.get(3, []) if isinstance(v, int)), 0),
                                next((v for v in p.get(5, []) if isinstance(v, int)), 0))
    except Exception:
        pass
    return 0, 0


def _agy_extract(db: Path) -> tuple[int, str, str, int, int]:
    """(steps, model, workspace, input tokens, output tokens) of one conversation."""
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        c = con.cursor()
        steps = c.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        counts: dict = {}
        for (data,) in c.execute("SELECT data FROM gen_metadata"):
            for m in _AGY_MODEL.findall(data or b""):
                v = m.decode("utf-8", "replace")
                if v != "gemini-default":
                    counts[v] = counts.get(v, 0) + 1
        model = max(counts, key=counts.__getitem__) if counts else "gemini"
        out_total = max_in = 0
        for (payload,) in c.execute("SELECT step_payload FROM steps WHERE step_type=15"):
            if payload:
                o, i = _agy_usage(payload)
                out_total += o
                max_in = max(max_in, i)
        workspace = ""
        row = c.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1").fetchone()
        if row and row[0]:
            hit = _AGY_WS.findall(row[0])
            if hit:
                workspace = hit[0].decode("utf-8", "replace").replace("file://", "")
        con.close()
        return steps, model, workspace, max_in, out_total
    except Exception:
        return 0, "gemini", "", 0, 0


def read_antigravity(root: Path, state: dict, full: bool) -> Iterator[dict]:
    conv_dir = root / "conversations"
    if not conv_dir.is_dir():
        return
    summary: dict = {}
    summary_db = root / "conversation_summaries.db"
    if summary_db.exists():
        try:
            con = sqlite3.connect(f"file:{summary_db.as_posix()}?mode=ro", uri=True)
            for cid, ws_json, lmt in con.execute(
                "SELECT conversation_id, workspace_uris, last_modified_time FROM conversation_summaries"
            ):
                project = ""
                try:
                    uris = json.loads(ws_json or "[]")
                    project = urlparse(uris[0]).path if uris else ""
                except (ValueError, IndexError):
                    pass
                summary[cid] = {"project": project, "ts": _parse_ts(lmt)}
            con.close()
        except sqlite3.Error:
            pass
    for db in sorted(conv_dir.glob("*.db")):
        cid = db.stem
        steps, model, workspace, inp, out = _agy_extract(db)
        if not steps:
            continue
        meta = summary.get(cid, {})
        ts = meta.get("ts") or datetime.fromtimestamp(db.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None).isoformat()
        t = turn("antigravity", cid, "session", meta.get("project") or workspace, model, ts,
                 inp=inp, out=out, total=inp + out, snapshot=True)
        if full or _snapshot_changed(state, f"agy:{cid}", t):
            yield t


# ── Gemini CLI: ~/.gemini/tmp/<project>/chats/session-*.jsonl ────────────────

def read_gemini_cli(root: Path, state: dict, full: bool) -> Iterator[dict]:
    tmp = root / "tmp"
    if not tmp.is_dir():
        return
    # projects.json maps each project path to its tmp/ folder name
    slugs = {v: k for k, v in (_read_json(root / "projects.json", {}).get("projects") or {}).items()}
    for fp in sorted(tmp.glob("*/chats/*.jsonl")):
        project = slugs.get(fp.parent.parent.name, fp.parent.parent.name)
        key = "gm:" + fp.relative_to(tmp).as_posix()
        offset = _incremental(state, key, fp, full)
        if offset is None:
            continue
        # Gemini CLI appends a message again each time it updates it (one file
        # held 56,651 lines for 908 messages): the id is the key, the last copy wins.
        messages: dict = {}
        end = offset
        for end, msg in _lines(fp, offset):
            if not msg or msg.get("type") != "gemini" or not isinstance(msg.get("tokens"), dict):
                continue
            tok = msg["tokens"]
            cached = _to_int(tok.get("cached"))
            thoughts = _to_int(tok.get("thoughts"))
            # input counts the cached part; thoughts are billed as output
            t = turn("gemini-cli", fp.stem, msg.get("id") or f"offset:{end}", project, msg.get("model") or "gemini",
                     _parse_ts(msg.get("timestamp")), inp=max(0, _to_int(tok.get("input")) - cached),
                     out=_to_int(tok.get("output")) + thoughts, cache_read=cached, reasoning=thoughts,
                     total=_to_int(tok.get("total")) or None, origin="interactive")
            messages[t["turn_key"]] = t
        yield from messages.values()
        _mark(state, key, fp, end)


# ── Copilot CLI: ~/.copilot/session-state/<id>/events.jsonl ──────────────────

def _copilot_session(fp: Path) -> tuple[str, Optional[str], dict]:
    """(cwd, start time, the latest per-model totals) of one session."""
    cwd, started, metrics = "", None, {}
    for _, event in _lines(fp, 0):
        if not event:
            continue
        data = event.get("data") or {}
        started = started or _parse_ts(event.get("timestamp")) or _ms_ts(data.get("sessionStartTime"))
        if event.get("type") == "session.start":
            cwd = (data.get("context") or {}).get("cwd") or cwd
        if data.get("modelMetrics"):
            metrics = data["modelMetrics"]
    return cwd, started, metrics


def read_copilot(root: Path, state: dict, full: bool) -> Iterator[dict]:
    for fp in sorted((root / "session-state").glob("*/events.jsonl")):
        key = "cp:" + fp.parent.name
        if _incremental(state, key, fp, full) is None:
            continue
        cwd, started, metrics = _copilot_session(fp)
        for model, m in metrics.items():
            u = m.get("usage") or {}
            cache_read, cache_write = _to_int(u.get("cacheReadTokens")), _to_int(u.get("cacheWriteTokens"))
            # inputTokens includes both cache parts
            yield turn("copilot", fp.parent.name, f"session:{model}", cwd, model, started,
                       inp=max(0, _to_int(u.get("inputTokens")) - cache_read - cache_write),
                       out=_to_int(u.get("outputTokens")), cache_read=cache_read, cache_write=cache_write,
                       reasoning=_to_int(u.get("reasoningTokens")), snapshot=True)
        _mark(state, key, fp, 0)
    # older CLIs kept per-session totals in data.db
    legacy = root / "data.db"
    if legacy.exists():
        try:
            con = sqlite3.connect(f"file:{legacy.as_posix()}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT id, created_at, updated_at, total_input_tokens, total_output_tokens, "
                "total_cached_tokens, total_reasoning_tokens FROM sessions"
            ).fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        for sid, created, updated, inp, out, cached, rsn in rows:
            inp, out, cached, rsn = map(_to_int, (inp, out, cached, rsn))
            if inp + out + cached + rsn == 0:
                continue
            t = turn("copilot", sid, "session", "", "copilot-auto", _parse_ts(created) or _parse_ts(updated),
                     inp=max(0, inp - cached), out=out, cache_read=cached, reasoning=rsn, total=inp + out,
                     snapshot=True)
            if full or _snapshot_changed(state, f"cpdb:{sid}", t):
                yield t


READERS = (
    ("claude_code", read_claude_code),
    ("codex", read_codex),
    ("antigravity", read_antigravity),
    ("gemini_cli", read_gemini_cli),
    ("copilot", read_copilot),
)


# ── project labels ────────────────────────────────────────────────────────────

def label_project(path: str, policy: str, salt: str) -> str:
    """full: the path as recorded; basename: the folder name only; hash: an
    opaque stable label. Windows and POSIX separators both count."""
    if not path or policy == "full":
        return path or ""
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1] or path
    if policy == "basename":
        return name
    return "p-" + hashlib.sha256((salt + path).encode()).hexdigest()[:10]


# ── sending ───────────────────────────────────────────────────────────────────

def post(cfg: dict, turns: list) -> dict:
    body = json.dumps({"collector": VERSION, "machine": cfg["machine"], "turns": turns}).encode()
    req = urllib.request.Request(
        cfg["hub"].rstrip("/") + "/api/ingest", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}",
                 "User-Agent": f"meterlex-collector/{VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _spool_path() -> Path:
    return config_dir() / "spool.jsonl"


def spool_append(turns: list) -> None:
    if not turns:
        return
    path = _spool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def spool_flush(cfg: dict) -> tuple[int, Optional[str]]:
    """Send everything spooled, a batch at a time. Returns (sent, error)."""
    path = _spool_path()
    if not path.exists():
        return 0, None
    pending = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(pending) > SPOOL_MAX:
        pending = pending[-SPOOL_MAX:]
    sent, error = 0, None
    while pending:
        batch = [json.loads(ln) for ln in pending[:BATCH]]
        try:
            post(cfg, batch)
        except urllib.error.HTTPError as exc:
            error = f"hub answered {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = f"hub unreachable: {exc}"
            break
        sent += len(batch)
        pending = pending[BATCH:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(ln + "\n" for ln in pending), encoding="utf-8")
    os.replace(tmp, path)
    return sent, error


# ── commands ──────────────────────────────────────────────────────────────────

def collect(cfg: dict, state: dict, full: bool) -> tuple[list, dict]:
    turns, counts = [], {}
    for name, reader in READERS:
        path = source_path(cfg, name)
        if not path.exists():
            continue
        try:
            found = list(reader(path, state, full))
        except Exception as exc:  # one broken source must not stop the others
            print(f"  {name}: skipped ({exc})", file=sys.stderr)
            continue
        counts[name] = len(found)
        turns.extend(found)
    for t in turns:
        t["project"] = label_project(t["project"], cfg["labels"], cfg.get("salt", ""))
    return turns, counts


def cmd_run(cfg: dict, dry_run: bool = False, full: bool = False) -> int:
    state_path = config_dir() / "state.json"
    state = _read_json(state_path, {})
    state.setdefault("files", {})
    state.setdefault("snapshots", {})
    turns, counts = collect(cfg, state, full)
    print(f"{cfg['machine']}: {len(turns)} new turn(s) " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    if dry_run:
        print("dry run: nothing sent, nothing saved. The first records as they would be sent:")
        for t in turns[:3]:
            print(json.dumps(t, indent=1))
        return 0
    if not cfg.get("hub") or not cfg.get("key"):
        print("not set up: run `setup --hub URL --key KEY` first", file=sys.stderr)
        return 2
    spool_append(turns)          # safe on disk before the offsets move on
    state["last_run"] = _now()
    _write_json(state_path, state)
    sent, error = spool_flush(cfg)
    state["last_sent"], state["last_error"] = sent, error
    _write_json(state_path, state)
    print(f"sent {sent}" + (f"; kept the rest for the next run ({error})" if error else ""))
    return 1 if error else 0


def cmd_setup(args) -> int:
    cfg = load_config()
    if args.labels not in LABELS:
        print(f"--labels must be one of {', '.join(LABELS)}", file=sys.stderr)
        return 2
    cfg.update(hub=args.hub.rstrip("/"), key=args.key, labels=args.labels)
    if args.machine:
        cfg["machine"] = args.machine
    cfg.setdefault("salt", secrets.token_hex(8))
    _write_json(config_dir() / "config.json", cfg)
    try:
        os.chmod(config_dir() / "config.json", 0o600)
    except OSError:
        pass
    print(f"saved {config_dir() / 'config.json'}: machine {cfg['machine']}, labels {cfg['labels']}, hub {cfg['hub']}")
    return 0


def cmd_status(cfg: dict) -> int:
    state = _read_json(config_dir() / "state.json", {})
    spooled = sum(1 for _ in open(_spool_path(), encoding="utf-8")) if _spool_path().exists() else 0
    print(f"meterlex collector {VERSION}")
    print(f"  config   {config_dir() / 'config.json'}")
    print(f"  machine  {cfg.get('machine')}  labels {cfg.get('labels')}  hub {cfg.get('hub', '(not set)')}")
    print(f"  key      {'set' if cfg.get('key') else '(not set)'}")
    for name, _ in READERS:
        p = source_path(cfg, name)
        print(f"  {name:<12} {p} {'' if p.exists() else '(absent)'}")
    print(f"  files tracked {len(state.get('files', {}))}, waiting to send {spooled}")
    print(f"  last run {state.get('last_run', 'never')}, sent {state.get('last_sent', 0)}, "
          f"error {state.get('last_error') or 'none'}")
    return 0


def cmd_install(every: int) -> int:
    script = str(Path(__file__).resolve())
    python = sys.executable
    log = config_dir() / "collector.log"
    config_dir().mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        label = "com.meterlex.collector"
        plist = HOME / "Library" / "LaunchAgents" / f"{label}.plist"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{python}</string><string>{script}</string><string>run</string></array>
  <key>StartInterval</key><integer>{every}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        done = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True)
        print(f"launchd agent {label}: every {every} s, log {log}" + (f" ({done.stderr.strip()})" if done.returncode else ""))
        return done.returncode
    if os.name == "nt":
        pythonw = Path(python).with_name("pythonw.exe")
        exe = str(pythonw if pythonw.exists() else python)  # pythonw: no console window every 5 minutes
        minutes = max(1, every // 60)
        done = subprocess.run(
            ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", "Meterlex collector",
             "/TR", f'"{exe}" "{script}" run'], capture_output=True, text=True,
        )
        print(done.stdout.strip() or done.stderr.strip())
        return done.returncode
    print(f"add to crontab: */{max(1, every // 60)} * * * * {python} {script} run >> {log} 2>&1")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send this machine's AI token counts to the Meterlex hub.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="save the hub URL, this machine's key and its project labels")
    s.add_argument("--hub", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--machine")
    s.add_argument("--labels", default="full", help="full | basename | hash")
    r = sub.add_parser("run", help="one pass: read new usage and send it")
    r.add_argument("--dry-run", action="store_true", help="show what would be sent; send and save nothing")
    r.add_argument("--full", action="store_true", help="re-read every file from the start")
    lp = sub.add_parser("loop", help="run every --every seconds")
    lp.add_argument("--every", type=int, default=300)
    sub.add_parser("status", help="configuration, sources and the last run")
    i = sub.add_parser("install", help="run automatically: launchd on macOS, a Scheduled Task on Windows")
    i.add_argument("--every", type=int, default=300)
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        return cmd_setup(args)
    cfg = load_config()
    if args.cmd == "run":
        return cmd_run(cfg, dry_run=args.dry_run, full=args.full)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "install":
        return cmd_install(args.every)
    while True:
        cmd_run(cfg)
        time.sleep(max(30, args.every))


if __name__ == "__main__":
    sys.exit(main())
