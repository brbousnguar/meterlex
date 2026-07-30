"""Three-source ingestion: Claude Code, Codex, Antigravity.

Claude Code  → ~/.claude/projects/**/*.jsonl  (assistant events)
Codex        → ~/.codex/sessions/**/*.jsonl   (token_count events)
Antigravity  → ~/.gemini/antigravity-cli/conversations/*.db  (per-conversation DBs)
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

from database import engine, create_db
from models import UsageTurn, ScanState
import pricing

log = logging.getLogger("agentic-spend.ingest")

CLAUDE_CODE_DIR = Path(os.getenv("CLAUDE_CODE_DIR", os.path.expanduser("~/.claude/projects")))
CODEX_DIR = Path(os.getenv("CODEX_DIR", os.path.expanduser("~/.codex/sessions")))
AGY_DIR = Path(os.getenv("AGY_DIR", os.path.expanduser("~/.gemini/antigravity-cli")))
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))

FREE_PROVIDERS = {"ollama-launch", "ollama-launch-codex-app", "ollama"}


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ── Claude Code ───────────────────────────────────────────────────────────────

def _ingest_claude_code(session: Session, full: bool = False) -> dict:
    if not CLAUDE_CODE_DIR.exists():
        log.warning("CLAUDE_CODE_DIR %s not found", CLAUDE_CODE_DIR)
        return {"files": 0, "turns_added": 0, "errors": 0}

    files = sorted(CLAUDE_CODE_DIR.rglob("*.jsonl"))
    turns_added = errors = 0
    fx = pricing.get_fx_rate(session)
    now = datetime.utcnow()

    for fp in files:
        rel = f"cc:{fp.relative_to(CLAUDE_CODE_DIR)}"
        try:
            size = fp.stat().st_size
        except OSError:
            continue

        state = session.get(ScanState, rel)
        if full or state is None or size < state.file_size:
            offset = 0
        else:
            offset = state.last_offset

        try:
            new_turns, new_offset, file_errors = _scan_claude_file(session, fp, rel, offset, fx, now)
        except Exception as exc:
            log.warning("error reading %s: %s", rel, exc)
            errors += 1
            continue

        turns_added += new_turns
        errors += file_errors
        session.merge(ScanState(file_path=rel, last_offset=new_offset, last_scanned_at=now, file_size=size))
        session.commit()

    return {"files": len(files), "turns_added": turns_added, "errors": errors}


def _scan_claude_file(session, fp, rel, offset, fx, now):
    new_turns = errors = 0

    with open(fp, "rb") as fh:
        fh.seek(offset)
        while True:
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n") and not raw.endswith(b"\r"):
                break
            offset += len(raw)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue

            if event.get("type") != "assistant":
                continue

            msg = event.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue

            uuid = event.get("uuid", "")
            if not uuid:
                continue

            session_id = event.get("sessionId", str(fp.stem))
            model_id = msg.get("model", "unknown")
            ts = _parse_ts(event.get("timestamp", "")) or now
            project = event.get("cwd", "")

            _src_check = "ollama" if (model_id.startswith("glm-") or model_id.startswith("local/")) else "claude-code"
            exists = session.exec(
                select(UsageTurn.id).where(
                    UsageTurn.source == _src_check,
                    UsageTurn.session_id == session_id,
                    UsageTurn.turn_key == uuid,
                )
            ).first()
            if exists:
                continue

            in_t = _to_int(usage.get("input_tokens"))
            out_t = _to_int(usage.get("output_tokens"))
            ca_r = _to_int(usage.get("cache_read_input_tokens"))
            ca_w = _to_int(usage.get("cache_creation_input_tokens"))
            tot = in_t + out_t + ca_r + ca_w

            cost_usd, price_src = pricing.compute_cost(session, "anthropic", model_id, {
                "input": in_t, "output": out_t, "cacheRead": ca_r, "cacheWrite": ca_w,
            })

            _src = "ollama" if (model_id.startswith("glm-") or model_id.startswith("local/")) else "claude-code"
            session.add(UsageTurn(
                source=_src,
                session_id=session_id,
                turn_key=uuid,
                project=project,
                model_id=model_id,
                ts=ts,
                input_tokens=in_t,
                output_tokens=out_t,
                cache_read=ca_r,
                cache_write=ca_w,
                reasoning_tokens=0,
                total_tokens=tot,
                cost_usd=cost_usd,
                cost_eur=round(cost_usd * fx, 8),
                fx_rate=fx,
                price_source=price_src,
                file_path=rel,
            ))
            new_turns += 1

    return new_turns, offset, errors


# ── Codex ─────────────────────────────────────────────────────────────────────

def _ingest_codex(session: Session, full: bool = False) -> dict:
    if not CODEX_DIR.exists():
        log.warning("CODEX_DIR %s not found", CODEX_DIR)
        return {"files": 0, "turns_added": 0, "errors": 0}

    files = sorted(CODEX_DIR.rglob("*.jsonl"))
    turns_added = errors = 0
    fx = pricing.get_fx_rate(session)
    now = datetime.utcnow()

    for fp in files:
        rel = f"cx:{fp.relative_to(CODEX_DIR)}"
        try:
            size = fp.stat().st_size
        except OSError:
            continue

        state = session.get(ScanState, rel)
        if full or state is None or size < state.file_size:
            offset = 0
        else:
            offset = state.last_offset

        try:
            new_turns, new_offset, file_errors = _scan_codex_file(session, fp, rel, offset, fx, now)
        except Exception as exc:
            log.warning("error reading %s: %s", rel, exc)
            errors += 1
            continue

        turns_added += new_turns
        errors += file_errors
        session.merge(ScanState(file_path=rel, last_offset=new_offset, last_scanned_at=now, file_size=size))
        session.commit()

    return {"files": len(files), "turns_added": turns_added, "errors": errors}


def _get_session_meta(fp: Path) -> tuple[str, str, str]:
    """Return (session_id, provider, cwd) from the first session_meta event."""
    try:
        with open(fp) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                    if e.get("type") == "session_meta":
                        p = e.get("payload", {})
                        sid = p.get("session_id") or p.get("id") or str(fp.stem)
                        provider = p.get("model_provider", "openai")
                        cwd = p.get("cwd", "")
                        return sid, provider, cwd
                except Exception:
                    pass
    except Exception:
        pass
    return str(fp.stem), "openai", ""


def _scan_codex_file(session, fp, rel, offset, fx, now):
    new_turns = errors = 0

    session_id, session_provider, project = _get_session_meta(fp)
    is_free = session_provider in FREE_PROVIDERS
    model_id = f"local/{session_provider}" if is_free else "gpt-5"

    with open(fp, "rb") as fh:
        fh.seek(offset)
        while True:
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n") and not raw.endswith(b"\r"):
                break
            offset += len(raw)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue

            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload", {})
            if payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            last = info.get("last_token_usage") or {}
            if not last or not isinstance(last, dict):
                continue

            ts_str = event.get("timestamp", "")
            turn_key = ts_str or f"offset:{offset}"
            ts = _parse_ts(ts_str) or now

            exists = session.exec(
                select(UsageTurn.id).where(
                    UsageTurn.source == "codex",
                    UsageTurn.session_id == session_id,
                    UsageTurn.turn_key == turn_key,
                )
            ).first()
            if exists:
                continue

            in_t = _to_int(last.get("input_tokens"))
            out_t = _to_int(last.get("output_tokens"))
            ca_r = _to_int(last.get("cached_input_tokens"))
            rsn = _to_int(last.get("reasoning_output_tokens"))
            tot = in_t + out_t

            if is_free:
                cost_usd, price_src = 0.0, "free"
            else:
                cost_usd, price_src = pricing.compute_cost(session, "openai", model_id, {
                    "input": in_t, "output": out_t, "cacheRead": ca_r,
                    "cacheWrite": 0, "reasoningTokens": rsn,
                })

            session.add(UsageTurn(
                source="codex",
                session_id=session_id,
                turn_key=turn_key,
                project=project,
                model_id=model_id,
                ts=ts,
                input_tokens=in_t,
                output_tokens=out_t,
                cache_read=ca_r,
                cache_write=0,
                reasoning_tokens=rsn,
                total_tokens=tot,
                cost_usd=cost_usd,
                cost_eur=round(cost_usd * fx, 8),
                fx_rate=fx,
                price_source=price_src,
                file_path=rel,
            ))
            new_turns += 1

    return new_turns, offset, errors


# ── Antigravity ───────────────────────────────────────────────────────────────

_AGY_MODEL_PAT = re.compile(rb'gemini[a-z0-9\-\.]+', re.I)
_AGY_WS_PAT    = re.compile(rb'file:///[^\x00-\x08\x0a-\x1f]+')


def _pv(data: bytes, pos: int) -> tuple[int, int]:
    """Parse varint from data[pos:]. Returns (value, new_pos)."""
    r = shift = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        r |= (b & 0x7F) << shift
        if not (b & 0x80):
            return r, pos
        shift += 7
    return r, pos


def _pf(data: bytes) -> dict[int, list]:
    """Shallow protobuf parse → {field_num: [raw_values]}."""
    out: dict[int, list] = {}
    pos, n = 0, len(data)
    while pos < n:
        try:
            tag, pos = _pv(data, pos)
        except Exception:
            break
        fn, wt = tag >> 3, tag & 7
        try:
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
    """Extract (output_tokens, cumulative_prompt_tokens) from a step_payload blob.

    Path: payload → field5 → field9 → {field3: out_tokens, field5: cum_in_tokens}
    """
    try:
        for f5 in _pf(payload).get(5, []):
            if not isinstance(f5, bytes):
                continue
            for f9 in _pf(f5).get(9, []):
                if not isinstance(f9, bytes):
                    continue
                f9p = _pf(f9)
                out_t  = next((v for v in f9p.get(3, []) if isinstance(v, int)), 0)
                cum_in = next((v for v in f9p.get(5, []) if isinstance(v, int)), 0)
                return out_t, cum_in
    except Exception:
        pass
    return 0, 0


def _agy_extract(db_path: Path) -> tuple[int, str, str, int, int]:
    """Return (step_count, model_id, workspace, input_tokens, output_tokens)."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM steps")
        step_count = c.fetchone()[0]

        # Most common model from gen_metadata blobs (skip generic 'gemini-default')
        model_counts: dict[str, int] = {}
        c.execute("SELECT data FROM gen_metadata")
        for (data,) in c.fetchall():
            for m in _AGY_MODEL_PAT.findall(data or b""):
                v = m.decode("utf-8", errors="replace")
                if v != "gemini-default":
                    model_counts[v] = model_counts.get(v, 0) + 1
        model_id = max(model_counts, key=model_counts.__getitem__) if model_counts else "gemini"

        # Token counts from AI response steps (step_type=15)
        total_out = 0
        max_cum_in = 0
        c.execute("SELECT step_payload FROM steps WHERE step_type=15")
        for (payload,) in c.fetchall():
            if not payload:
                continue
            out_t, cum_in = _agy_usage(payload)
            total_out += out_t
            if cum_in > max_cum_in:
                max_cum_in = cum_in

        # Workspace from trajectory_metadata_blob
        workspace = ""
        c.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1")
        row = c.fetchone()
        if row and row[0]:
            matches = _AGY_WS_PAT.findall(row[0])
            if matches:
                workspace = matches[0].decode("utf-8", errors="replace").replace("file://", "")

        conn.close()
        return step_count, model_id, workspace, max_cum_in, total_out
    except Exception as exc:
        log.debug("agy extract %s: %s", db_path.name, exc)
        return 0, "gemini", "", 0, 0


def _ingest_antigravity(session: Session, full: bool = False) -> dict:
    conv_dir = AGY_DIR / "conversations"
    if not conv_dir.exists():
        log.warning("Antigravity conversations dir %s not found", conv_dir)
        return {"files": 0, "turns_added": 0, "errors": 0}

    # Load summary DB for timestamps and workspace fallback
    summary: dict[str, dict] = {}
    agy_db = AGY_DIR / "conversation_summaries.db"
    if agy_db.exists():
        try:
            conn = sqlite3.connect(str(agy_db))
            c = conn.cursor()
            c.execute("SELECT conversation_id, workspace_uris, last_modified_time FROM conversation_summaries")
            for cid, ws_json, lmt in c.fetchall():
                project = ""
                try:
                    uris = json.loads(ws_json or "[]")
                    if uris:
                        project = urlparse(uris[0]).path
                except Exception:
                    pass
                ts = None
                try:
                    ts = datetime.fromisoformat(str(lmt).replace("+00:00", "").rstrip("Z"))
                except Exception:
                    pass
                summary[cid] = {"project": project, "ts": ts}
            conn.close()
        except Exception as exc:
            log.warning("antigravity summary DB read failed: %s", exc)

    dbs = [p for p in sorted(conv_dir.glob("*.db")) if not ("-shm" in p.name or "-wal" in p.name)]
    turns_added = errors = 0

    fx = pricing.get_fx_rate(session)

    for db_path in dbs:
        conv_id = db_path.stem
        try:
            step_count, model_id, workspace, in_t, out_t = _agy_extract(db_path)
            if step_count == 0:
                continue

            tot = in_t + out_t
            cost_usd, price_src = pricing.compute_cost(session, "google", model_id, {
                "input": in_t, "output": out_t, "cacheRead": 0, "cacheWrite": 0,
            })

            meta    = summary.get(conv_id, {})
            project = meta.get("project") or workspace
            ts      = meta.get("ts") or datetime.utcfromtimestamp(db_path.stat().st_mtime)

            existing = session.exec(
                select(UsageTurn).where(
                    UsageTurn.source == "antigravity",
                    UsageTurn.session_id == conv_id,
                    UsageTurn.turn_key == "session",
                )
            ).first()

            if existing:
                if (existing.input_tokens == in_t and existing.output_tokens == out_t
                        and existing.model_id == model_id):
                    continue
                existing.input_tokens  = in_t
                existing.output_tokens = out_t
                existing.total_tokens  = tot
                existing.model_id      = model_id
                existing.project       = project
                existing.ts            = ts
                existing.cost_usd      = cost_usd
                existing.cost_eur      = round(cost_usd * fx, 8)
                existing.price_source  = price_src
                session.add(existing)
            else:
                session.add(UsageTurn(
                    source="antigravity",
                    session_id=conv_id,
                    turn_key="session",
                    project=project,
                    model_id=model_id,
                    ts=ts,
                    input_tokens=in_t,
                    output_tokens=out_t,
                    cache_read=0,
                    cache_write=0,
                    reasoning_tokens=0,
                    total_tokens=tot,
                    cost_usd=cost_usd,
                    cost_eur=round(cost_usd * fx, 8),
                    fx_rate=fx,
                    price_source=price_src,
                    file_path=str(db_path),
                ))
                turns_added += 1

        except Exception as exc:
            log.warning("error reading antigravity conv %s: %s", conv_id, exc)
            errors += 1

    try:
        session.commit()
    except Exception as exc:
        log.error("antigravity commit failed: %s", exc)
        errors += 1

    return {"files": len(dbs), "turns_added": turns_added, "errors": errors}


# ── scan loop ─────────────────────────────────────────────────────────────────

def scan_once(session: Session, full: bool = False) -> dict:
    cc = _ingest_claude_code(session, full)
    cx = _ingest_codex(session, full)
    agy = _ingest_antigravity(session, full)
    total = cc["turns_added"] + cx["turns_added"] + agy["turns_added"]
    log.info("scan full=%s cc=%s cx=%s agy=%s total=%d", full, cc, cx, agy, total)
    return {"claude_code": cc, "codex": cx, "antigravity": agy, "total_turns_added": total}


async def ingest_loop():
    create_db()
    with Session(engine) as session:
        pricing.seed_prices(session)
    with Session(engine) as session:
        scan_once(session, full=True)

    while True:
        await asyncio.sleep(SCAN_INTERVAL_SEC)
        try:
            with Session(engine) as session:
                scan_once(session, full=False)
        except Exception as exc:
            log.error("incremental scan failed: %s", exc)


def trigger_scan(full: bool = False) -> dict:
    with Session(engine) as session:
        return scan_once(session, full=full)


def recompute_costs() -> int:
    with Session(engine) as session:
        fx = pricing.get_fx_rate(session)
        rows = session.exec(select(UsageTurn)).all()
        n = 0
        for t in rows:
            if t.source in ("claude-code", "codex"):
                provider = "anthropic" if t.source == "claude-code" else "openai"
                usage = {
                    "input": t.input_tokens, "output": t.output_tokens,
                    "cacheRead": t.cache_read, "cacheWrite": t.cache_write,
                    "reasoningTokens": t.reasoning_tokens,
                }
                t.cost_usd, t.price_source = pricing.compute_cost(session, provider, t.model_id, usage)
            elif t.source == "antigravity":
                usage = {
                    "input": t.input_tokens, "output": t.output_tokens,
                    "cacheRead": 0, "cacheWrite": 0,
                }
                t.cost_usd, t.price_source = pricing.compute_cost(session, "google", t.model_id, usage)
            else:
                t.cost_usd, t.price_source = 0.0, "free"
            t.cost_eur = round(t.cost_usd * fx, 8)
            t.fx_rate = fx
            session.add(t)
            n += 1
            if n % 500 == 0:
                session.commit()
        session.commit()
        return n
