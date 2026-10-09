#!/usr/bin/env python3
"""Meterlex collector: reads the AI coding tools' session logs on this machine
and sends their token counts to the Meterlex hub.

Only counts leave the machine: tool, model, time, session id, project label and
token numbers. Prompts, replies, tool output and file contents are never read
into what is sent. `run --dry-run` prints exactly what would go.

    python meterlex_collector.py setup --hub URL --key KEY [--machine NAME] [--labels full|basename|hash] [--path SOURCE=DIR]
    python meterlex_collector.py run [--dry-run] [--full] [--reattribute]
    python meterlex_collector.py loop [--every 300]
    python meterlex_collector.py status
    python meterlex_collector.py rollup [--apply]
    python meterlex_collector.py install      # launchd on macOS, a Scheduled Task on Windows, a systemd timer on Linux
    python meterlex_collector.py statusline   # Claude Code's status line: records the Claude Max quota

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
import shutil
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

VERSION = "0.5.0"
BATCH = 1000               # turns per POST
SPOOL_MAX = 500_000        # unsent turns kept on disk before the oldest are dropped
HOME = Path.home()

DEFAULT_PATHS = {
    "claude_code": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "antigravity": "~/.gemini/antigravity-cli",
    "gemini_cli": "~/.gemini",
    "copilot": "~/.copilot",
    "openclaw": "~/.openclaw",
    "hermes": "~/.hermes",
    # NeMo Relay writes a Hermes run's trajectory wherever its config says, so
    # there is no default: `setup --path hermes_relay=DIR` turns it on.
    "hermes_relay": None,
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


def source_path(cfg: dict, name: str) -> Optional[Path]:
    """Where a source's logs are, or None for a source with no default that
    this machine has not pointed anywhere."""
    path = cfg["paths"].get(name) or DEFAULT_PATHS[name]
    return Path(os.path.expanduser(path)) if path else None


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
         cache_write=0, reasoning=0, total=None, origin=None, snapshot=False, alt_keys=None,
         branch=None) -> dict:
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
    if branch:
        t["branch"] = branch
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
    if b.get("branch") and not a.get("branch"):
        m["branch"] = b["branch"]
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


# ── projects: a folder rolls up to its repository ─────────────────────────────

# Folders that belong to the project around them, even when they hold a git
# checkout of their own (SwiftPM and CocoaPods clone dependencies with .git).
_DEPENDENCY_DIRS = {"node_modules", ".build", "Pods", "Carthage", "DerivedData", ".venv", "venv",
                    "vendor", "site-packages", ".gradle", "target"}
_root_cache: dict = {}


def _is_absolute(path: str) -> bool:
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")


_WORKTREE_DIR = re.compile(r"^(.+)-wt$")


def _main_checkout(git_file: Path) -> Optional[Path]:
    """A worktree's `.git` is a file naming the main checkout's git folder
    (`gitdir: /repo/.git/worktrees/x`); its work belongs to that checkout."""
    try:
        text = git_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    for parent in gitdir.parents:
        if parent.name == ".git":
            return parent.parent
    return None


def _repository(path: str) -> Optional[str]:
    """The repository a folder or file belongs to, or None outside every one.

    - the nearest parent with a `.git`, cut above any dependency folder;
    - a git worktree counts toward its main checkout;
    - a worktree folder that is gone, named `<repo>-wt/<name>` (the layout
      these machines use), counts toward `<repo>` when that repository exists.
    """
    if not path or not _is_absolute(path):
        return None
    if path in _root_cache:
        return _root_cache[path]
    parts = re.split(r"[\\/]", path)
    for i, part in enumerate(parts):
        if part in _DEPENDENCY_DIRS:
            parts = parts[:i]
            break
    for i, part in enumerate(parts):
        m = _WORKTREE_DIR.match(part)
        if m and i + 1 < len(parts):
            sibling = parts[:i] + [m.group(1)]
            main = Path(os.sep.join(sibling)) if sibling[0] else Path("/" + "/".join(sibling[1:]))
            if (main / ".git").exists():
                parts = sibling
            break
    p = Path(os.sep.join(parts)) if parts[0] else Path("/" + "/".join(parts[1:]))
    root = None
    candidate = None
    for candidate in [p, *p.parents]:
        try:
            marker = candidate / ".git"
            if marker.is_dir():
                root = str(candidate)
            elif marker.is_file():
                main = _main_checkout(marker)
                root = str(main if main and (main / ".git").is_dir() else candidate)
            else:
                continue
        except OSError:
            pass
        break
    if root and candidate is not None and candidate != p:
        root = _unignored_project(candidate, p, root)
    _root_cache[path] = root
    return root


_ignore_cache: dict = {}


def _unignored_project(repo_dir: Path, p: Path, root: str) -> str:
    """A folder the repository ignores is not part of it: `~/Server` ignores
    `webapps/`, so `~/Server/webapps/time-tracker` (no repository of its own)
    is its own project, named by the folder just below the ignored one. Asks
    git; without git, the repository is kept."""
    rel = p.relative_to(repo_dir).parts
    prefixes = ["/".join(rel[:i + 1]) + "/" for i in range(len(rel))]
    key = (str(repo_dir), prefixes[-1])
    if key not in _ignore_cache:
        try:
            done = subprocess.run(["git", "-C", str(repo_dir), "check-ignore", "--stdin"],
                                  input="\n".join(prefixes), capture_output=True, text=True, timeout=10)
            ignored = {ln.strip() for ln in done.stdout.splitlines()}
        except (OSError, subprocess.SubprocessError):
            ignored = set()
        _ignore_cache[key] = ignored
    ignored = _ignore_cache[key]
    for i, prefix in enumerate(prefixes):
        if prefix in ignored:
            if i + 1 < len(rel) and _holds_repositories(repo_dir.joinpath(*rel[:i + 1])):
                return str(repo_dir.joinpath(*rel[:i + 2]))
            return root
    return root


def _holds_repositories(folder: Path) -> bool:
    """An ignored folder holding other repositories (`~/Server/webapps/`) is a
    shelf of projects; one that holds none (`dist/`, `data/`) is part of the
    repository around it."""
    key = str(folder)
    if key not in _ignore_cache:
        try:
            _ignore_cache[key] = any((c / ".git").exists() for c in folder.iterdir() if c.is_dir())
        except OSError:
            _ignore_cache[key] = False
    return _ignore_cache[key]


def project_root(path: str) -> str:
    """The project a working folder counts toward: its repository, or the
    folder itself outside every repository (or once it is gone from this
    machine, when there is nothing left to look at)."""
    return _repository(path) or path or ""


def _is_inside(child: str, parent: str) -> bool:
    return child != parent and child.startswith(parent.rstrip("\\/") + ("\\" if "\\" in parent else "/"))


_CD = re.compile(r"""^\s*cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))""")


def _touched_paths(message: dict, cwd: str) -> list:
    """Absolute paths one reply's tool calls worked on: the files it read or
    edited, the folders it searched, and a Bash `cd` target. Read locally to
    pick the project; the paths themselves are never sent."""
    found = []
    content = message.get("content")
    if not isinstance(content, list):
        return found
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        args = block.get("input") or {}
        if not isinstance(args, dict):
            continue
        for key in ("file_path", "notebook_path", "path"):
            v = args.get(key)
            if isinstance(v, str) and v:
                # a file is never a project: its folder is what counts
                is_file = key != "path" or Path(v).is_file()
                found.append(os.path.dirname(v) if is_file else v)
        cmd = args.get("command")
        if isinstance(cmd, str):
            m = _CD.match(cmd)
            if m:
                target = os.path.expanduser(next(g for g in m.groups() if g))
                if not _is_absolute(target) and cwd:
                    target = os.path.normpath(os.path.join(cwd, target))
                found.append(target)
    return [f for f in found if _is_absolute(f)]


def attribute(cwd: str, touched: list, focus: Optional[str]) -> tuple[str, Optional[str]]:
    """(project, new focus) for one reply.

    The project is the repository of the reply's working folder, unless the
    reply worked on files in another repository: a session started in
    ~/Server that edits ~/Server/webapps/x, or ~/Projects/y, counts toward
    that one. Files outside every repository (scratch files, ~/.claude) do
    not name a project. A reply that touches no repository stays with the
    last other one the session worked in, until it works on its own again:
    the text that explains an edit belongs to the same project as the edit."""
    base = project_root(cwd)
    repos = [r for r in (_repository(t) for t in touched) if r]
    other = [r for r in repos if r != base]
    if other:
        best = max(set(other), key=lambda r: (other.count(r), -other.index(r)))
        return best, best
    if repos:  # worked on its own repository
        return base, None
    if focus:  # touched nothing that is a project: stays with the last one
        return focus, focus
    return base, None


def _branch(name, cwd: str) -> Optional[str]:
    """The reply's git branch, when its folder is in a repository. Claude Code
    records `HEAD` outside every repository (and on a detached checkout), which
    names nothing."""
    if not name or name == "HEAD" or not _repository(cwd):
        return None
    return name


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
        seen = {} if offset == 0 else (state["files"].get(key) or {})
        focus, start = seen.get("focus"), seen.get("start")
        for end, event in _lines(fp, offset):
            if start is None and event and event.get("cwd"):
                start = project_root(event["cwd"])  # the session's first folder
            if not event or event.get("type") != "assistant":
                continue
            msg = event.get("message") or {}
            usage = msg.get("usage")
            uuid = event.get("uuid")
            if not usage or not uuid:
                continue
            msg_id = msg.get("id")
            cwd = event.get("cwd", "")
            project, focus = attribute(cwd, _touched_paths(msg, cwd), focus)
            t = turn(
                "claude-code", event.get("sessionId") or fp.stem, msg_id or uuid, project,
                msg.get("model", "unknown"), _parse_ts(event.get("timestamp")),
                inp=_to_int(usage.get("input_tokens")), out=_to_int(usage.get("output_tokens")),
                cache_read=_to_int(usage.get("cache_read_input_tokens")),
                cache_write=_to_int(usage.get("cache_creation_input_tokens")),
                origin=_claude_origin(event), alt_keys=[uuid] if msg_id else None,
                # Claude Code records the branch of the repository the session started
                # in, whatever the working folder: it names nothing in another one
                branch=_branch(event.get("gitBranch"), cwd) if project == start else None,
            )
            k = (t["session_id"], t["turn_key"])
            if k in replies:
                merged = _merge_max(replies[k], t)
                if t["project"] != project_root(cwd):
                    merged["project"] = t["project"]  # a later part of the reply named another repo
                    merged.pop("branch", None)
                    if t.get("branch"):
                        merged["branch"] = t["branch"]
                replies[k] = merged
            else:
                replies[k] = t
        yield from replies.values()
        _mark(state, key, fp, end, focus=focus, start=start)


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


# ── OpenClaw agents: ~/.openclaw/agents/<id>/agent/openclaw-agent.sqlite ─────

# agent:<id>:<channel>[:<kind>:<address>] — the channel says who started the run.
OPENCLAW_AUTOMATED = {"cron", "main", "explicit", "heartbeat", "schedule"}


def _openclaw_origin(session_key: str) -> str:
    """A run started by a schedule, by another agent, or by a person in a chat.
    `main` is the agent's own session, which is where its unattended work runs."""
    parts = (session_key or "").split(":")
    channel = parts[2] if len(parts) > 2 else ""
    if channel in OPENCLAW_AUTOMATED:
        return "automated"
    if channel == "agent":
        return "subagent"
    return "interactive"


def _hosted_model(provider: str, model_id: str) -> str:
    """The rate card keys resold models by their host: `ollama/<m>:cloud`,
    `openrouter/<vendor>/<m>`. A model called by its own vendor keeps its id."""
    if not model_id:
        return "unknown"
    if provider in ("ollama", "openrouter") and not model_id.startswith(f"{provider}/"):
        return f"{provider}/{model_id}"
    return model_id


def read_openclaw(root: Path, state: dict, full: bool) -> Iterator[dict]:
    for db in sorted(root.glob("agents/*/agent/openclaw-agent.sqlite")):
        agent = db.parent.parent.name
        key = f"oc:{agent}"
        seen = state["files"].get(key) or {}
        # created_at is epoch milliseconds; it is the watermark, because the
        # rows live in a database that rewrites itself, not an append-only log.
        watermark = 0 if full else int(seen.get("watermark") or 0)
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT created_at, event_json FROM trajectory_runtime_events "
                "WHERE created_at > ? ORDER BY created_at", (watermark,),
            ).fetchall()
        except sqlite3.Error:
            continue
        newest = watermark
        for created_at, blob in rows:
            newest = max(newest, int(created_at or 0))
            try:
                event = json.loads(blob)
            except (TypeError, ValueError):
                continue
            if event.get("type") != "model.completed":
                continue
            usage = ((event.get("data") or {}).get("usage")) or {}
            if not usage:
                continue
            yield turn(
                "openclaw",
                event.get("sessionId") or agent,
                f'{event.get("runId") or "run"}:{event.get("seq")}',
                agent,                      # the agent is the "where" of an agent run
                _hosted_model(event.get("provider") or "", event.get("modelId") or ""),
                _parse_ts(event.get("ts")) or _ms_ts(created_at),
                inp=_to_int(usage.get("input")), out=_to_int(usage.get("output")),
                cache_read=_to_int(usage.get("cacheRead")), cache_write=_to_int(usage.get("cacheWrite")),
                total=_to_int(usage.get("total")) or None,
                origin=_openclaw_origin(event.get("sessionKey") or ""),
            )
        con.close()
        _mark(state, key, db, 0, watermark=newest)


# ── Hermes Agent: ~/.hermes/state.db, and NeMo Relay trajectories ────────────
#
# Hermes keeps each session's usage in its own database. A run traced by NeMo
# Relay also leaves an ATIF trajectory, and a tutorial run deletes its Hermes
# home on exit, so the trajectory can be the only record. Both name a session
# by Hermes's own id and count it per model under the same key, so a session
# found in both is stored once.

HERMES_AUTOMATED = {"cron", "api_server", "batch"}
_HERMES_SESSION = re.compile(r"^(\d{8}_\d{6}_[0-9a-f]+):")


def _hermes_origin(source: str, parent: Optional[str]) -> str:
    if parent or source == "delegate":
        return "subagent"
    return "automated" if source in HERMES_AUTOMATED else "interactive"


def _hermes_key(model: str, task: str) -> str:
    """`task` is empty for the agent's own loop; side work (titles,
    compression, vision) is named and kept apart."""
    return f"session:{model}" + (f":{task}" if task else "")


def _hermes_homes(root: Path) -> list:
    homes = [root] + sorted(p for p in (root / "profiles").glob("*") if p.is_dir() and not p.name.startswith("."))
    return [h / "state.db" for h in homes if (h / "state.db").exists()]


_HERMES_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def _select(con: sqlite3.Connection, table: str, columns: tuple) -> str:
    """SELECT the columns a table has, NULL for the ones an older Hermes did
    not have yet."""
    have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    return "SELECT " + ", ".join(c if c in have else f"NULL AS {c}" for c in columns) + f" FROM {table}"


def _hermes_db(db: Path) -> Iterator[dict]:
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sessions = {r["id"]: dict(r) for r in con.execute(_select(con, "sessions", (
            "id", "source", "parent_session_id", "started_at", "model", "billing_provider", "cwd", "git_branch",
            *_HERMES_FIELDS)))}
        usage = con.execute(_select(con, "session_model_usage", (
            "session_id", "model", "billing_provider", "task", *_HERMES_FIELDS,
        ))).fetchall() if "session_model_usage" in tables else []
        con.close()
    except sqlite3.Error:
        return
    fields = _HERMES_FIELDS
    parts: dict = {}           # (session, model, task) -> counts
    main: dict = {}            # session -> its main-loop counts already split by model
    for u in usage:
        sid = u["session_id"]
        if sid not in sessions:
            continue
        k = (sid, _hosted_model(u["billing_provider"] or "", u["model"] or ""), u["task"] or "")
        acc = parts.setdefault(k, dict.fromkeys(fields, 0))
        for f in fields:
            acc[f] += _to_int(u[f])
            if not u["task"]:
                main.setdefault(sid, dict.fromkeys(fields, 0))[f] += _to_int(u[f])
    # The session row holds the main loop's totals. Usage the per-model table
    # missed (a gateway writes absolute totals; databases older than the table
    # have none) is what remains, and goes to the session's model.
    for sid, s in sessions.items():
        split = main.get(sid, dict.fromkeys(fields, 0))
        rest = {f: max(0, _to_int(s[f]) - split[f]) for f in fields}
        if any(rest.values()):
            acc = parts.setdefault((sid, _hosted_model(s["billing_provider"] or "", s["model"] or ""), ""),
                                   dict.fromkeys(fields, 0))
            for f in fields:
                acc[f] += rest[f]
    for (sid, model, task), c in parts.items():
        if not any(c[f] for f in fields[:4]):
            continue
        s = sessions[sid]
        cwd = s["cwd"] or ""
        yield turn("hermes", sid, _hermes_key(model, task), project_root(cwd), model,
                   _ms_ts(float(s["started_at"]) * 1000) if s["started_at"] else None,
                   inp=c["input_tokens"], out=c["output_tokens"], cache_read=c["cache_read_tokens"],
                   cache_write=c["cache_write_tokens"], reasoning=c["reasoning_tokens"], snapshot=True,
                   origin=_hermes_origin(s["source"] or "", s["parent_session_id"]),
                   branch=_branch(s["git_branch"], cwd))


def read_hermes(root: Path, state: dict, full: bool) -> Iterator[dict]:
    """One snapshot per session and model, from each Hermes home's state.db
    (the default home and every profile)."""
    for db in _hermes_homes(root):
        for t in _hermes_db(db):
            if full or _snapshot_changed(state, f"hm:{t['session_id']}:{t['turn_key']}", t):
                yield t


def _relay_trajectory(data: dict) -> Optional[tuple[str, str, str]]:
    """(Hermes session id, provider, execution surface) of a Hermes ATIF
    trajectory, or None for any other file. Relay names each API request
    `<session>:<turn>:…`, which is where Hermes's own session id survives."""
    if not str(data.get("schema_version", "")).startswith("ATIF") or \
            "hermes" not in str((data.get("agent") or {}).get("name", "")).lower():
        return None
    events = ((data.get("extra") or {}).get("observed_events")) or []
    session, providers, surface = None, {}, ""
    for e in events:
        meta = e.get("metadata") or {}
        if not session:
            m = _HERMES_SESSION.match(str(meta.get("api_request_id") or meta.get("turn_id") or ""))
            session = m.group(1) if m else None
        if meta.get("hermes.provider"):
            providers[meta["hermes.provider"]] = providers.get(meta["hermes.provider"], 0) + 1
        surface = surface or meta.get("hermes.execution_surface") or ""
    provider = max(providers, key=providers.__getitem__) if providers else ""
    return session or str(data.get("session_id") or ""), provider, surface


def read_hermes_relay(root: Path, state: dict, full: bool) -> Iterator[dict]:
    """One snapshot per session and model, from the ATIF trajectories NeMo
    Relay writes for Hermes runs. The project is the repository that holds the
    run's files."""
    for fp in sorted(root.rglob("trajectory-*.json")):
        key = "hr:" + fp.relative_to(root).as_posix()
        if _incremental(state, key, fp, full) is None:
            continue
        data = _read_json(fp, {})
        found = _relay_trajectory(data) if isinstance(data, dict) else None
        _mark(state, key, fp, 0)
        if not found or not found[0]:
            continue
        session, provider, surface = found
        by_model: dict = {}
        started = None
        for step in data.get("steps") or []:
            started = started or _parse_ts(step.get("timestamp"))
            m = step.get("metrics")
            if step.get("source") != "agent" or not isinstance(m, dict):
                continue
            model = _hosted_model(provider, step.get("model_name") or (data.get("agent") or {}).get("model_name") or "")
            cached = _to_int(m.get("cached_tokens"))
            details = ((m.get("extra") or {}).get("completion_tokens_details")) or {}
            acc = by_model.setdefault(model, [0, 0, 0, 0])
            acc[0] += max(0, _to_int(m.get("prompt_tokens")) - cached)   # prompt_tokens counts the cached part
            acc[1] += _to_int(m.get("completion_tokens"))
            acc[2] += cached
            acc[3] += _to_int(details.get("reasoning_tokens")) if isinstance(details, dict) else 0
        for model, (inp, out, cache_read, reasoning) in by_model.items():
            yield turn("hermes", session, _hermes_key(model, ""), project_root(str(fp.parent)), model, started,
                       inp=inp, out=out, cache_read=cache_read, reasoning=reasoning, snapshot=True,
                       origin=_hermes_origin(surface, None))


READERS = (
    ("claude_code", read_claude_code),
    ("codex", read_codex),
    ("antigravity", read_antigravity),
    ("gemini_cli", read_gemini_cli),
    ("copilot", read_copilot),
    ("openclaw", read_openclaw),
    ("hermes", read_hermes),
    ("hermes_relay", read_hermes_relay),
)


# ── project labels ────────────────────────────────────────────────────────────

def label_branch(branch: str, policy: str, salt: str) -> str:
    """A branch name can say what a private project is about, so a `hash`
    machine sends it hashed too."""
    if not branch or policy != "hash":
        return branch or ""
    return "b-" + hashlib.sha256((salt + branch).encode()).hexdigest()[:10]


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

def _hub(cfg: dict, path: str, body: Optional[dict] = None) -> dict:
    req = urllib.request.Request(
        cfg["hub"].rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}",
                 "User-Agent": f"meterlex-collector/{VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_stored(project: str) -> Optional[str]:
    """The repository a stored project label belongs to, judged on this disk.

    Only a folder that still exists is resolved (and a gone `<repo>-wt/<name>`
    worktree whose repository exists): a folder that is gone might have been a
    repository of its own, so it keeps its name rather than being folded into
    the parent."""
    if not project or not _is_absolute(project):
        return None
    gone_worktree = bool(re.search(r"-wt[\\/]", project)) and not Path(project).exists()
    if not Path(project).exists() and not gone_worktree:
        return None
    repo = _repository(project)
    if gone_worktree and repo and not re.search(r"[\\/]" + re.escape(Path(repo).name) + r"-wt[\\/]", project):
        return None  # the worktree's own repository is gone too
    return repo if repo and repo != project else None


def cmd_rollup(cfg: dict, apply: bool = False) -> int:
    """Move this machine's rows stored under a folder to that folder's
    repository, for history whose transcripts are gone. Full-path labels only."""
    if not cfg.get("hub") or not cfg.get("key"):
        print("not set up: run `setup --hub URL --key KEY` first", file=sys.stderr)
        return 2
    if cfg.get("labels") != "full":
        print(f"labels are {cfg.get('labels')}: only full paths can be resolved", file=sys.stderr)
        return 2
    stored = _hub(cfg, "/api/projects/mine")["projects"]
    renames = {p: r for p in stored if (r := resolve_stored(p))}
    for old, new in sorted(renames.items()):
        print(f"  {old} -> {new}")
    print(f"{len(renames)} of {len(stored)} stored folder(s) belong to a repository")
    if not apply:
        print("report only: add --apply to move them")
        return 0
    print(json.dumps(_hub(cfg, "/api/projects/rename", {"renames": renames})))
    return 0


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


# ── the Claude Max quota ──────────────────────────────────────────────────────
# Claude Code hands its status line the subscription's rate limits (documented
# as `rate_limits.<window>.used_percentage` / `.resets_at`). `statusline` keeps
# each new reading in quota.jsonl; `run` sends what it has not sent yet.

WEEK_S = 7 * 24 * 3600
QUOTA_KEEP = 256 * 1024     # bytes of sent readings kept before quota.jsonl is trimmed


def _quota_path() -> Path:
    return config_dir() / "quota.jsonl"


def quota_readings(data: dict, now: float) -> list:
    """The windows in one status-line payload, as readings."""
    out = []
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    for window, w in (limits or {}).items():
        if not isinstance(w, dict) or window == "spend_limit":
            continue
        try:
            used, resets = float(w["used_percentage"]), float(w["resets_at"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"window": window, "used_pct": used, "resets_at": int(resets),
                    "ts": datetime.fromtimestamp(now, tz=timezone.utc).isoformat()})
    return out


def _newer(r: dict, kept) -> bool:
    """Within one window the percentage only grows. Every open Claude Code
    session repaints its status line with the last figure it was given, so an
    idle session from days ago still shows an old, lower one: only a higher
    figure, or a new window, is news."""
    if not kept:
        return True
    pct, resets = kept
    if abs(r["resets_at"] - resets) > 3600:
        return r["resets_at"] > resets
    return r["used_pct"] > pct


def record_quota(readings: list) -> int:
    """Append the readings that are news for their window (see _newer)."""
    last_path = config_dir() / "quota-last.json"
    last = _read_json(last_path, {})
    fresh = [r for r in readings if _newer(r, last.get(r["window"]))]
    if not fresh:
        return 0
    config_dir().mkdir(parents=True, exist_ok=True)
    with open(_quota_path(), "a", encoding="utf-8") as fh:
        for r in fresh:
            fh.write(json.dumps(r) + "\n")
    for r in fresh:
        last[r["window"]] = [r["used_pct"], r["resets_at"]]
    _write_json(last_path, last)
    return len(fresh)


def status_text(data: dict, readings: list, now: float) -> str:
    """One short line: model · folder · week used and its pace · the 5-hour window."""
    parts = []
    model = (data.get("model") or {}).get("display_name")
    folder = (data.get("workspace") or {}).get("current_dir") or data.get("cwd")
    if model:
        parts.append(str(model))
    if folder:
        parts.append(Path(str(folder)).name or str(folder))
    by = {r["window"]: r for r in readings}
    week = by.get("seven_day")
    if week:
        elapsed = min(1.0, max(0.0, 1 - (week["resets_at"] - now) / WEEK_S)) * 100
        delta = week["used_pct"] - elapsed
        pace = ("on pace" if abs(delta) <= 2
                else f"{abs(delta):.0f} pts {'ahead' if delta > 0 else 'behind'}")
        parts.append(f"week {week['used_pct']:.0f}% · {pace}")
    if by.get("five_hour"):
        parts.append(f"5h {by['five_hour']['used_pct']:.0f}%")
    return " · ".join(parts)


def cmd_statusline(stdin=None) -> int:
    """Never fails: a status line that errors blanks Claude Code's footer."""
    now = time.time()
    try:
        data = json.loads((stdin or sys.stdin).read() or "{}")
    except (ValueError, OSError):
        data = {}
    readings = quota_readings(data, now)
    try:
        record_quota(readings)
        # an idle session's own figure can be days old: show the newest known
        best = _read_json(config_dir() / "quota-last.json", {})
        for r in readings:
            kept = best.get(r["window"])
            if kept and abs(kept[1] - r["resets_at"]) <= 3600:
                r["used_pct"] = max(r["used_pct"], kept[0])
    except OSError:
        pass
    print(status_text(data if isinstance(data, dict) else {}, readings, now))
    return 0


def quota_flush(cfg: dict, state: dict) -> tuple[int, Optional[str]]:
    """Send the readings past state['quota_offset']; trim the file once it grows."""
    path = _quota_path()
    if not path.exists():
        return 0, None
    offset = int(state.get("quota_offset", 0))
    size = path.stat().st_size
    if offset > size:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n") + 1      # a line still being written waits for the next run
    readings = []
    for line in chunk[:end].splitlines():
        try:
            readings.append(json.loads(line))
        except ValueError:
            continue
    if readings:
        body = {"collector": VERSION, "machine": cfg["machine"], "turns": [], "quota": readings}
        try:
            _hub(cfg, "/api/ingest", body)
        except urllib.error.HTTPError as exc:
            return 0, f"hub answered {exc.code} to the quota readings"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return 0, f"hub unreachable for the quota readings: {exc}"
    offset += end
    if offset > QUOTA_KEEP:
        with open(path, "rb") as fh:
            fh.seek(offset)
            rest = fh.read()
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(rest)
        os.replace(tmp, path)
        offset = 0
    state["quota_offset"] = offset
    return len(readings), None


# ── commands ──────────────────────────────────────────────────────────────────

def collect(cfg: dict, state: dict, full: bool, reattribute: bool = False) -> tuple[list, dict]:
    turns, counts = [], {}
    for name, reader in READERS:
        path = source_path(cfg, name)
        if path is None or not path.exists():
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
        if t.get("branch"):
            t["branch"] = label_branch(t["branch"], cfg["labels"], cfg.get("salt", ""))
        if reattribute:
            t["reattribute"] = True
    return turns, counts


def cmd_run(cfg: dict, dry_run: bool = False, full: bool = False, reattribute: bool = False) -> int:
    state_path = config_dir() / "state.json"
    state = _read_json(state_path, {})
    state.setdefault("files", {})
    state.setdefault("snapshots", {})
    turns, counts = collect(cfg, state, full or reattribute, reattribute)
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
    quota_sent, quota_error = quota_flush(cfg, state)
    error = error or quota_error
    state["last_sent"], state["last_error"] = sent, error
    _write_json(state_path, state)
    print(f"sent {sent}" + (f", {quota_sent} quota reading(s)" if quota_sent else "")
          + (f"; kept the rest for the next run ({error})" if error else ""))
    return 1 if error else 0


def cmd_setup(args) -> int:
    cfg = load_config()
    if args.labels not in LABELS:
        print(f"--labels must be one of {', '.join(LABELS)}", file=sys.stderr)
        return 2
    for item in args.path or []:
        name, sep, folder = item.partition("=")
        if not sep or name not in DEFAULT_PATHS:
            print(f"--path takes SOURCE=DIR, SOURCE one of {', '.join(DEFAULT_PATHS)}", file=sys.stderr)
            return 2
        if folder:
            cfg["paths"][name] = folder
        else:
            cfg["paths"].pop(name, None)      # SOURCE= puts it back to its default
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
        if p is None:
            print(f"  {name:<12} (not set: setup --path {name}=DIR)")
        else:
            print(f"  {name:<12} {p} {'' if p.exists() else '(absent)'}")
    print(f"  files tracked {len(state.get('files', {}))}, waiting to send {spooled}")
    last_quota = _read_json(config_dir() / "quota-last.json", {})
    print(f"  quota    " + (", ".join(f"{w} {v[0]:.0f}%" for w, v in last_quota.items())
                            or "no reading yet (statusLine → `statusline`)"))
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
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        return _install_systemd(python, script, log, every)
    print(f"add to crontab: */{max(1, every // 60)} * * * * {python} {script} run >> {log} 2>&1")
    return 0


def _install_systemd(python: str, script: str, log: Path, every: int) -> int:
    """A user service and its timer. A user's timers stop at logout unless
    the account lingers, which only root can switch on."""
    units = HOME / ".config" / "systemd" / "user"
    units.mkdir(parents=True, exist_ok=True)
    (units / "meterlex-collector.service").write_text(f"""[Unit]
Description=Meterlex collector: send this machine's AI token counts to the hub

[Service]
Type=oneshot
ExecStart={python} {script} run
StandardOutput=append:{log}
StandardError=append:{log}
""", encoding="utf-8")
    (units / "meterlex-collector.timer").write_text(f"""[Unit]
Description=Run the Meterlex collector every {every} s

[Timer]
OnBootSec=60
OnUnitActiveSec={every}
Persistent=true

[Install]
WantedBy=timers.target
""", encoding="utf-8")
    for cmd in (["daemon-reload"], ["enable", "--now", "meterlex-collector.timer"]):
        done = subprocess.run(["systemctl", "--user", *cmd], capture_output=True, text=True)
        if done.returncode:
            print(f"systemctl --user {' '.join(cmd)}: {done.stderr.strip()}", file=sys.stderr)
            return done.returncode
    print(f"systemd timer meterlex-collector.timer: every {every} s, log {log}")
    user = os.environ.get("USER", "")
    linger = subprocess.run(["loginctl", "show-user", user, "-p", "Linger", "--value"], capture_output=True, text=True)
    if linger.stdout.strip() != "yes":
        print(f"it stops when you log out; to keep it running: sudo loginctl enable-linger {user}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send this machine's AI token counts to the Meterlex hub.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="save the hub URL, this machine's key and its project labels")
    s.add_argument("--hub", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--machine")
    s.add_argument("--labels", default="full", help="full | basename | hash")
    s.add_argument("--path", action="append", metavar="SOURCE=DIR",
                   help="read a source from DIR (repeatable; SOURCE= restores the default)")
    r = sub.add_parser("run", help="one pass: read new usage and send it")
    r.add_argument("--dry-run", action="store_true", help="show what would be sent; send and save nothing")
    r.add_argument("--full", action="store_true", help="re-read every file from the start")
    r.add_argument("--reattribute", action="store_true",
                   help="re-read every file and replace the project and branch the hub stored for each reply")
    lp = sub.add_parser("loop", help="run every --every seconds")
    lp.add_argument("--every", type=int, default=300)
    sub.add_parser("status", help="configuration, sources and the last run")
    ru = sub.add_parser("rollup", help="move this machine's stored folders to their repositories (history)")
    ru.add_argument("--apply", action="store_true", help="change the hub (default: report only)")
    i = sub.add_parser("install", help="run automatically: launchd on macOS, a Scheduled Task on Windows, "
                                       "a systemd user timer on Linux")
    i.add_argument("--every", type=int, default=300)
    sub.add_parser("statusline", help="Claude Code's status line: record the Claude Max quota, print one line")
    args = ap.parse_args(argv)

    if args.cmd == "statusline":
        return cmd_statusline()

    if args.cmd == "setup":
        return cmd_setup(args)
    cfg = load_config()
    if args.cmd == "run":
        return cmd_run(cfg, dry_run=args.dry_run, full=args.full, reattribute=args.reattribute)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "rollup":
        return cmd_rollup(cfg, apply=args.apply)
    if args.cmd == "install":
        return cmd_install(args.every)
    while True:
        cmd_run(cfg)
        time.sleep(max(30, args.every))


if __name__ == "__main__":
    sys.exit(main())
