"""The hub's side of ingestion.

Collectors (collector/meterlex_collector.py) read the session logs on each
machine and POST usage turns to /api/ingest; the hub never reads transcripts.
This module stores those turns without double counting, decides which tool
each one counts toward, and prices it.
"""
import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from database import engine, create_db
from models import Machine, UsageTurn
import pricing

log = logging.getLogger("meterlex.ingest")

SOURCES = {"claude-code", "ollama", "codex", "antigravity", "gemini-cli", "copilot", "openclaw"}
ORIGINS = {"interactive", "automated", "subagent"}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read", "cache_write", "reasoning_tokens")
MAX_TURNS_PER_BATCH = 5000
PROVIDER_BY_SOURCE = {
    "claude-code": "anthropic",
    "codex": "openai",
    "antigravity": "google",
    "gemini-cli": "google",
    "ollama": "ollama",
    "copilot": "github",
}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def classify_source(source: str, model_id: str) -> str:
    """Claude Code runs non-Claude models through Ollama (`ollama launch
    claude`): those count toward Ollama, not the Claude subscription.
    `<synthetic>` is Claude Code's own placeholder, never a model call."""
    if (source == "claude-code" and model_id and not model_id.startswith("claude-")
            and model_id not in {"<synthetic>", "synthetic", "unknown"}):
        return "ollama"
    return source


def label_project(project: str, labels: str) -> str:
    """Enforce a machine's label policy on the hub too, so a misconfigured
    collector can't store a full path from a basename-only machine."""
    if not project or labels == "full":
        return project
    name = re.split(r"[\\/]", project.rstrip("\\/"))[-1] or project
    if labels == "basename":
        return name
    if project.startswith("p-") and not re.search(r"[\\/]", project):
        return project  # already hashed by the collector
    return "p-" + hashlib.sha256(project.encode()).hexdigest()[:10]


def _branch_name(value) -> str:
    """`HEAD` is what Claude Code records outside a repository: no branch."""
    name = str(value or "").strip()
    return "" if name == "HEAD" else name


def label_branch(branch: str, labels: str) -> str:
    """A `hash` machine's branch names are stored hashed, like its projects."""
    if not branch or labels != "hash" or branch.startswith("b-"):
        return branch
    return "b-" + hashlib.sha256(branch.encode()).hexdigest()[:10]


def _ts(value) -> datetime:
    """Stored times are naive UTC, as collectors send them."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.utcnow()
    return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


def _normalize(raw: dict, machine: Machine) -> dict:
    source = str(raw["source"])
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    model = str(raw.get("model_id") or "unknown")[:200]
    t = {
        "source": classify_source(source, model),
        "session_id": str(raw["session_id"])[:200],
        "turn_key": str(raw["turn_key"])[:200],
        "model_id": model,
        "ts": _ts(raw.get("ts")),
        "project": label_project(str(raw.get("project") or ""), machine.labels)[:500],
        "origin": raw.get("origin") if raw.get("origin") in ORIGINS else None,
        "branch": label_branch(_branch_name(raw.get("branch")), machine.labels)[:200] or None,
        "snapshot": bool(raw.get("snapshot")),
        "reattribute": bool(raw.get("reattribute")),
        "alt_keys": [str(k)[:200] for k in (raw.get("alt_keys") or [])][:50],
    }
    for f in TOKEN_FIELDS:
        t[f] = max(0, int(raw.get(f) or 0))
    t["total_tokens"] = max(0, int(raw.get("total_tokens") or 0)) or (
        t["input_tokens"] + t["output_tokens"] + t["cache_read"] + t["cache_write"]
    )
    return t


def _get(session: Session, source: str, session_id: str, turn_key: str) -> Optional[UsageTurn]:
    return session.exec(select(UsageTurn).where(
        UsageTurn.source == source, UsageTurn.session_id == session_id, UsageTurn.turn_key == turn_key,
    )).first()


def _price(session: Session, row: UsageTurn, fx: float) -> None:
    usage = {
        "input": row.input_tokens, "output": row.output_tokens, "cacheRead": row.cache_read,
        "cacheWrite": row.cache_write, "reasoningTokens": row.reasoning_tokens,
    }
    row.cost_usd, row.price_source = pricing.compute_cost(
        session, PROVIDER_BY_SOURCE.get(row.source, ""), row.model_id, usage, source=row.source,
    )
    row.cost_eur = round(row.cost_usd * fx, 8)
    row.fx_rate = fx


def ingest_turns(session: Session, machine: Machine, turns: list, collector: Optional[str] = None) -> dict:
    """Store one batch from a machine's collector.

    - A reply seen again keeps each token count at its largest: a Claude reply
      is logged once per part, and its numbers only grow while it streams.
    - `alt_keys` name rows the same reply was stored under before replies were
      keyed by message id (one row per logged part); those fold into one.
    - `snapshot` rows (a session's running totals) replace their values.
    """
    fx = pricing.get_fx_rate(session)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "folded": 0, "rejected": 0}
    for raw in turns:
        try:
            t = _normalize(raw, machine)
        except (KeyError, TypeError, ValueError):
            counts["rejected"] += 1
            continue
        row = _get(session, t["source"], t["session_id"], t["turn_key"])
        if not t["snapshot"]:
            legacy = []
            for k in t["alt_keys"]:
                if k != t["turn_key"]:
                    old = _get(session, t["source"], t["session_id"], k)
                    if old is not None and old is not row and old not in legacy:
                        legacy.append(old)
            if row is None and legacy:
                row = legacy.pop(0)
                row.turn_key = t["turn_key"]
            for old in legacy:  # row is set whenever legacy is non-empty
                for f in TOKEN_FIELDS:
                    setattr(row, f, max(getattr(row, f), getattr(old, f)))
                session.delete(old)
                counts["folded"] += 1

        if row is None:
            row = UsageTurn(
                source=t["source"], session_id=t["session_id"], turn_key=t["turn_key"],
                project=t["project"], model_id=t["model_id"], ts=t["ts"], machine=machine.name,
                origin=t["origin"], branch=t["branch"], total_tokens=t["total_tokens"],
                **{f: t[f] for f in TOKEN_FIELDS},
            )
            _price(session, row, fx)
            session.add(row)
            session.flush()
            counts["inserted"] += 1
            continue

        before = tuple(getattr(row, f) for f in TOKEN_FIELDS) + (row.model_id, row.project, row.machine, row.origin, row.branch)
        if t["snapshot"]:
            for f in TOKEN_FIELDS:
                setattr(row, f, t[f])
            row.total_tokens = t["total_tokens"]
            row.model_id, row.ts, row.project = t["model_id"], t["ts"], t["project"]
        else:
            for f in TOKEN_FIELDS:
                setattr(row, f, max(getattr(row, f), t[f]))
            row.total_tokens = row.input_tokens + row.output_tokens + row.cache_read + row.cache_write
            # A reply keeps the project it was first stored under, unless the
            # collector re-reads its transcript to attribute it again.
            if t["reattribute"] and t["project"]:
                row.project = t["project"]
            else:
                row.project = row.project or t["project"]
        row.machine = machine.name
        row.origin = row.origin or t["origin"]
        row.branch = t["branch"] if t["reattribute"] and t["branch"] else (row.branch or t["branch"])
        after = tuple(getattr(row, f) for f in TOKEN_FIELDS) + (row.model_id, row.project, row.machine, row.origin, row.branch)
        if after != before:
            _price(session, row, fx)
            session.add(row)
            session.flush()
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1

    machine.last_seen_at = datetime.utcnow()
    machine.last_batch = len(turns)
    machine.turns_received += counts["inserted"] + counts["updated"]
    machine.collector_version = collector or machine.collector_version
    session.add(machine)
    session.commit()
    return {"accepted": len(turns) - counts["rejected"], **counts}


def reclassify_existing(session: Session) -> int:
    """Apply classify_source to stored rows: non-Claude models run through
    Claude Code count toward Ollama. Idempotent."""
    from sqlalchemy import text

    result = session.execute(text(
        "UPDATE OR IGNORE usage_turns SET source='ollama' "
        "WHERE source='claude-code' AND model_id NOT LIKE 'claude-%' "
        "AND model_id NOT IN ('<synthetic>', 'synthetic', 'unknown')"
    ))
    session.commit()
    return result.rowcount or 0


def machine_projects(session: Session, machine: Machine) -> list:
    """The project labels stored for one machine, for its collector to resolve."""
    return sorted(p for p in session.exec(
        select(UsageTurn.project).where(UsageTurn.machine == machine.name, UsageTurn.project.is_not(None)).distinct()
    ).all() if p)


def rename_projects(session: Session, machine: Machine, renames: dict) -> dict:
    """Apply a collector's {stored label: repository} renames to its own rows.

    Only the machine can tell a repository from a plain folder (it has the
    disk), so the hub never guesses: it moves exactly what the collector
    resolved, for that machine, with its label policy enforced."""
    folders = rows = 0
    for old, new in renames.items():
        if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
            continue
        new = label_project(new, machine.labels)[:500]
        if new == old:
            continue
        matching = session.exec(select(UsageTurn).where(
            UsageTurn.machine == machine.name, UsageTurn.project == old)).all()
        if matching:
            folders += 1
        for row in matching:
            row.project = new
            session.add(row)
            rows += 1
    session.commit()
    return {"folders_moved": folders, "rows_moved": rows}


def dedupe_legacy(session: Session, apply: bool = False) -> dict:
    """Fold old Claude Code rows that were stored once per logged part of a
    reply (keyed by per-line uuid) into one row per reply.

    Rows whose transcript is still on disk are folded exactly when a collector
    re-sends them (by message id and alt_keys). This handles the rest: in one
    session, consecutive uuid-keyed rows of one model with identical input and
    cache counts, within five minutes, are one reply (a new request always
    carries a different cache count as the context grows). The group keeps its
    largest output count.
    """
    rows = session.exec(select(UsageTurn).where(
        UsageTurn.source.in_(("claude-code", "ollama"))
    ).order_by(UsageTurn.session_id, UsageTurn.model_id, UsageTurn.ts)).all()
    groups, current, prev = [], [], None
    for r in rows:
        if not _UUID.match(r.turn_key):
            continue
        key = (r.session_id, r.model_id, r.input_tokens, r.cache_read, r.cache_write)
        if prev is not None and key == prev[0] and (r.ts - prev[1]).total_seconds() <= 300:
            current.append(r)
        else:
            if len(current) > 1:
                groups.append(current)
            current = [r]
        prev = (key, r.ts)
    if len(current) > 1:
        groups.append(current)

    removed = sum(len(g) - 1 for g in groups)
    tokens_before = sum(r.total_tokens for g in groups for r in g)
    tokens_after = sum(max(r.output_tokens for r in g) + g[0].input_tokens + g[0].cache_read + g[0].cache_write
                       for g in groups)
    if apply:
        fx = pricing.get_fx_rate(session)
        for g in groups:
            keep = g[0]
            keep.output_tokens = max(r.output_tokens for r in g)
            keep.total_tokens = keep.input_tokens + keep.output_tokens + keep.cache_read + keep.cache_write
            _price(session, keep, fx)
            session.add(keep)
            for r in g[1:]:
                session.delete(r)
        session.commit()
    return {"replies": len(groups), "rows_removed": removed, "tokens_before": tokens_before,
            "tokens_after": tokens_after, "applied": apply}


async def price_loop():
    """Pull the shared rate card at startup and reprice history when it
    changed; the weekly cron calls /api/prices/mirror-pull for later changes."""
    create_db()
    with Session(engine) as session:
        result = await pricing.mirror_pull_prices(session)
    if result.get("synced"):
        n = await asyncio.to_thread(recompute_costs)
        log.info("repriced %d turns from the mirrored rate card", n)


def recompute_costs() -> int:
    """Reprice every stored turn from the current rate card."""
    with Session(engine) as session:
        fx = pricing.get_fx_rate(session)
        n = 0
        for t in session.exec(select(UsageTurn)).all():
            _price(session, t, fx)
            session.add(t)
            n += 1
            if n % 1000 == 0:
                session.commit()
        session.commit()
        return n
