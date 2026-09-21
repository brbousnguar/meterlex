"""Meterlex API — AI coding usage from every machine, priced.

Collectors on each machine POST their usage to /api/ingest with the machine's
key; everything else is read-only views over the stored turns.
Ports: API 8692, UI 5180 (both bound to 127.0.0.1; reach the UI over Tailscale).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, func

from database import engine, create_db, get_session
from models import UsageTurn, ModelPrice, Setting, ManualBill, Machine
import pricing
import ingest
import machines

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("meterlex")

TOOLS = ["claude-code", "codex", "antigravity", "gemini-cli", "ollama", "copilot", "openclaw"]

# Periods are named in the user's own time, not UTC: a "week" that starts at
# 02:00 Paris on Monday reads as wrong to the person looking at it.
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Paris"))

DEFAULT_SETTINGS = {
    "fx_rate": "0.92",
    "sub_claude_code_eur": "21.60",
    "sub_codex_eur": "23.00",
    "sub_antigravity_eur": "18.33",
    "sub_gemini_cli_eur": "0.00",
    "sub_ollama_eur": "18.18",
    "sub_copilot_eur": "0.00",
    # OpenClaw agents run on the plans already paid for above (mostly Claude
    # Max); charging a fee here again would count it twice.
    "sub_openclaw_eur": "0.00",
}
SETTING_KEYS = set(DEFAULT_SETTINGS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()
    with Session(engine) as session:
        for k, v in DEFAULT_SETTINGS.items():
            if not session.get(Setting, k):
                session.add(Setting(key=k, value=v))
        session.commit()
        moved = ingest.reclassify_existing(session)
        if moved:
            log.info("moved %d non-Claude Claude Code turns to Ollama", moved)
    task = asyncio.create_task(ingest.price_loop())
    yield
    task.cancel()


app = FastAPI(title="Meterlex API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"bad date '{s}' (use YYYY-MM-DD)")


def _local_now() -> datetime:
    """Wall-clock time where the user is, naive, for period arithmetic."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def _to_utc(local: datetime) -> datetime:
    """A naive local wall-clock time as the naive UTC that rows are stored in."""
    return local.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def _from_utc(utc: datetime) -> datetime:
    """The inverse: naive stored UTC as naive local wall-clock time."""
    return utc.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).replace(tzinfo=None)


def _midnight(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _period_bounds(period: str, ref: Optional[str]):
    """(start, end) in UTC for a period named in local time.

    Periods are local: a week starts Monday 00:00 in LOCAL_TZ and a month at
    local midnight, so "this week" does not change at 02:00 the way UTC bounds
    did. Rows are stored as naive UTC, so both ends come back converted.
    """
    now = _local_now()
    if period == "weekly":
        day = _parse_date(ref) if ref else now          # any day inside the week
        start = _midnight(day) - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    elif period == "monthly":
        if ref:
            try:
                y, m = map(int, ref.split("-"))
                start = datetime(y, m, 1)
            except (TypeError, ValueError):
                raise HTTPException(400, "monthly ref must use YYYY-MM")
        else:
            start = _midnight(now).replace(day=1)
        end = start.replace(month=start.month % 12 + 1) if start.month < 12 \
            else start.replace(year=start.year + 1, month=1)
    elif period == "daily":
        if ref:
            start = _midnight(_parse_date(ref))
            end = start + timedelta(days=1)
        else:
            start, end = now - timedelta(days=30), now
    elif period == "yearly":
        try:
            y = int(ref) if ref else now.year
        except (TypeError, ValueError):
            raise HTTPException(400, "yearly ref must use YYYY")
        start, end = datetime(y, 1, 1), datetime(y + 1, 1, 1)
    else:
        raise HTTPException(400, "period must be weekly, monthly, daily, or yearly")
    return _to_utc(start), _to_utc(end)


def _previous_bounds(period: str, start_utc: datetime):
    """The same-length period before `start_utc`, for "vs last week" figures."""
    start = _from_utc(start_utc)
    if period == "weekly":
        return _period_bounds("weekly", (start - timedelta(days=7)).strftime("%Y-%m-%d"))
    if period == "monthly":
        prev = start.replace(day=1) - timedelta(days=1)
        return _period_bounds("monthly", prev.strftime("%Y-%m"))
    if period == "yearly":
        return _period_bounds("yearly", str(start.year - 1))
    return None, None


def _where(start, end, source: Optional[str] = None, machine: Optional[str] = None) -> list:
    f = [UsageTurn.ts >= start, UsageTurn.ts < end]
    if source:
        f.append(UsageTurn.source == source)
    if machine:
        f.append(UsageTurn.machine == machine)
    return f


# ── health / ingest ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    total = session.exec(select(func.count(UsageTurn.id))).one()
    last = session.exec(select(func.max(UsageTurn.created_at))).one()
    by_source = session.exec(
        select(UsageTurn.source, func.count(UsageTurn.id)).group_by(UsageTurn.source)
    ).all()
    by_machine = session.exec(
        select(UsageTurn.machine, func.count(UsageTurn.id)).group_by(UsageTurn.machine)
    ).all()
    return {
        "status": "ok",
        "total_turns": total,
        "last_ingested_at": last.isoformat() if last else None,
        "by_source": {s: n for s, n in by_source},
        "by_machine": {m: n for m, n in by_machine},
    }


@app.post("/api/ingest")
def api_ingest(payload: dict, authorization: Optional[str] = Header(None),
               session: Session = Depends(get_session)):
    """A collector's batch. The machine is the one its key belongs to."""
    machine = machines.authenticate(session, authorization)
    if machine is None:
        raise HTTPException(401, "unknown or revoked machine key")
    turns = payload.get("turns")
    if not isinstance(turns, list):
        raise HTTPException(400, "turns must be a list")
    if len(turns) > ingest.MAX_TURNS_PER_BATCH:
        raise HTTPException(413, f"at most {ingest.MAX_TURNS_PER_BATCH} turns per batch")
    result = ingest.ingest_turns(session, machine, turns, collector=payload.get("collector"))
    return {"machine": machine.name, **result}


@app.get("/api/config")
def config():
    """What the UI needs to know about this deployment. The rate card lives in
    its own app; its address is per-machine, so it is configuration, not code."""
    return {
        "prices_url": os.getenv("PRICES_URL", ""),
        "openclaw_spend_url": os.getenv("OPENCLAW_SPEND_URL", ""),
        "tz": str(LOCAL_TZ),
        "hub_machine": os.getenv("HUB_MACHINE", ""),
    }


@app.get("/api/machines")
def list_machines(session: Session = Depends(get_session)):
    counts = {m: (n, tok) for m, n, tok in session.exec(
        select(UsageTurn.machine, func.count(UsageTurn.id), func.sum(UsageTurn.total_tokens))
        .group_by(UsageTurn.machine)
    ).all()}
    known = {m.name: m for m in session.exec(select(Machine)).all()}
    out = []
    for name in sorted(set(known) | set(counts)):
        m = known.get(name)
        n, tok = counts.get(name, (0, 0))
        out.append({
            "name": name,
            "labels": m.labels if m else "full",
            "registered": m is not None,
            "revoked": bool(m and m.revoked),
            "last_seen_at": m.last_seen_at.isoformat() if m and m.last_seen_at else None,
            "collector_version": m.collector_version if m else None,
            "turns": int(n), "total_tokens": int(tok or 0),
        })
    return out


@app.post("/api/recompute")
def api_recompute():
    return {"rows_updated": ingest.recompute_costs()}


# ── summary / spend ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary(
    period: str = "monthly",
    ref: Optional[str] = None,
    machine: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Per-tool cost summary + subscription comparison."""
    start, end = _period_bounds(period, ref)
    rows = session.exec(
        select(
            UsageTurn.source,
            func.count(UsageTurn.id),
            func.sum(UsageTurn.cost_eur),
            func.sum(UsageTurn.input_tokens),
            func.sum(UsageTurn.output_tokens),
            func.sum(UsageTurn.total_tokens),
        ).where(*_where(start, end, machine=machine)).group_by(UsageTurn.source)
    ).all()

    year_month = start.strftime("%Y-%m")

    data = {}
    for source, turns, cost, inp, out, tok in rows:
        sub = pricing.get_sub_eur(session, source, year_month)
        cost = round(cost or 0, 4)
        data[source] = {
            "source": source,
            "turns": int(turns),
            "cost_eur": cost,
            "sub_eur": sub,
            "savings_eur": round(cost - sub, 4),
            "input_tokens": int(inp or 0),
            "output_tokens": int(out or 0),
            "total_tokens": int(tok or 0),
        }

    # Fill in tools with no data this period
    for tool in TOOLS:
        if tool not in data:
            sub = pricing.get_sub_eur(session, tool, year_month)
            data[tool] = {
                "source": tool, "turns": 0, "cost_eur": 0.0,
                "sub_eur": sub, "savings_eur": round(0 - sub, 4),
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            }

    total_cost = sum(d["cost_eur"] for d in data.values())
    total_sub = sum(d["sub_eur"] for d in data.values())
    return {
        "period": period, "ref": ref, "machine": machine,
        "from": start.isoformat(), "to": end.isoformat(),
        "total_cost_eur": round(total_cost, 4),
        "total_sub_eur": round(total_sub, 4),
        "total_savings_eur": round(total_cost - total_sub, 4),
        "tools": list(data.values()),
    }


@app.get("/api/spend")
def spend(
    period: str = "monthly",
    source: Optional[str] = None,
    machine: Optional[str] = None,
    ref: Optional[str] = None,
    frm: Optional[str] = None,
    to: Optional[str] = None,
    session: Session = Depends(get_session),
):
    if frm or to:
        start = _parse_date(frm) or datetime.utcfromtimestamp(0)
        end = _parse_date(to) or datetime.utcnow()
    else:
        start, end = _period_bounds(period, ref)

    base = _where(start, end, source, machine)

    total_eur = session.exec(select(func.sum(UsageTurn.cost_eur)).where(*base)).one() or 0.0
    total_usd = session.exec(select(func.sum(UsageTurn.cost_usd)).where(*base)).one() or 0.0
    turns = session.exec(select(func.count(UsageTurn.id)).where(*base)).one() or 0

    def grouped(col):
        return session.exec(
            select(
                col,
                func.sum(UsageTurn.cost_eur),
                func.count(UsageTurn.id),
                func.sum(UsageTurn.input_tokens),
                func.sum(UsageTurn.output_tokens),
                func.sum(UsageTurn.total_tokens),
            ).where(*base).group_by(col)
        ).all()

    def rows(col, key, empty):
        return sorted(
            [{key: v or empty, "cost_eur": round(c or 0, 4), "turns": int(n),
              "input_tokens": int(inp or 0), "output_tokens": int(out or 0),
              "total_tokens": int(tok or 0)}
             for v, c, n, inp, out, tok in grouped(col)],
            key=lambda x: (-x["cost_eur"], -x["total_tokens"]),
        )

    year_month = start.strftime("%Y-%m")
    sub = pricing.get_sub_eur(session, source, year_month) if source else sum(
        pricing.get_sub_eur(session, t, year_month) for t in TOOLS
    )
    return {
        "source": source, "machine": machine,
        "from": start.isoformat(), "to": end.isoformat(),
        "total_usd": round(total_usd, 4),
        "total_eur": round(total_eur, 4),
        "sub_eur": sub,
        "turns": int(turns),
        "by_model": rows(UsageTurn.model_id, "model_id", "(unknown)"),
        "by_project": rows(UsageTurn.project, "project", "(no cwd)"),
        "by_branch": rows(UsageTurn.branch, "branch", "(no branch)"),
        "by_machine": rows(UsageTurn.machine, "machine", "(unknown)"),
        "by_origin": rows(UsageTurn.origin, "origin", "(unrecorded)"),
    }


@app.get("/api/spend/timeseries")
def spend_timeseries(
    period: str = "monthly",
    source: Optional[str] = None,
    machine: Optional[str] = None,
    ref: Optional[str] = None,
    session: Session = Depends(get_session),
):
    start, end = _period_bounds(period, ref)
    fmt = "%Y-%m" if period == "yearly" else "%Y-%m-%d"
    base = _where(start, end, source, machine)

    bucket_expr = func.strftime(fmt, UsageTurn.ts)

    def empty(b):
        return {"bucket": b, "cost_eur": 0.0, "turns": 0, "by_source": {}, "tokens_by_source": {},
                "by_model": {}, "tokens_by_model": {}, "tokens_by_machine": {}}

    buckets: dict = {}
    for b, src, cost, n, tok in session.exec(
        select(bucket_expr, UsageTurn.source, func.sum(UsageTurn.cost_eur), func.count(UsageTurn.id),
               func.sum(UsageTurn.total_tokens)).where(*base).group_by(bucket_expr, UsageTurn.source)
    ).all():
        entry = buckets.setdefault(b, empty(b))
        cost = round(cost or 0, 4)
        entry["cost_eur"] = round(entry["cost_eur"] + cost, 4)
        entry["turns"] += int(n)
        entry["by_source"][src] = round(entry["by_source"].get(src, 0) + cost, 4)
        entry["tokens_by_source"][src] = entry["tokens_by_source"].get(src, 0) + int(tok or 0)

    # Per-bucket by-model split (for the "spend by model" chart view).
    for b, mid, cost, tok in session.exec(
        select(bucket_expr, UsageTurn.model_id, func.sum(UsageTurn.cost_eur), func.sum(UsageTurn.total_tokens))
        .where(*base).group_by(bucket_expr, UsageTurn.model_id)
    ).all():
        entry = buckets.setdefault(b, empty(b))
        mid = mid or "(unknown)"
        entry["by_model"][mid] = round(entry["by_model"].get(mid, 0) + (cost or 0), 4)
        entry["tokens_by_model"][mid] = entry["tokens_by_model"].get(mid, 0) + int(tok or 0)

    for b, mach, tok in session.exec(
        select(bucket_expr, UsageTurn.machine, func.sum(UsageTurn.total_tokens))
        .where(*base).group_by(bucket_expr, UsageTurn.machine)
    ).all():
        entry = buckets.setdefault(b, empty(b))
        entry["tokens_by_machine"][mach] = int(tok or 0)

    return [buckets[k] for k in sorted(buckets)]


# ── overview ──────────────────────────────────────────────────────────────────

_TOKEN_COLS = ("input_tokens", "output_tokens", "cache_read", "cache_write", "reasoning_tokens")


def _bucket_key(hour_utc: str, period: str) -> str:
    """The local day (or month, over a year) an hour of UTC belongs to."""
    local = _from_utc(datetime.strptime(hour_utc, "%Y-%m-%d %H"))
    return local.strftime("%Y-%m" if period == "yearly" else "%Y-%m-%d")


def _buckets_for(period: str, start_utc: datetime, end_utc: datetime) -> list:
    """Every bucket in the period, including the empty ones, so a quiet day
    shows as a gap in the chart instead of disappearing."""
    start, end = _from_utc(start_utc), _from_utc(end_utc)
    out, cur = [], start
    if period == "yearly":
        cur = start.replace(day=1)
        while cur < end:
            out.append(cur.strftime("%Y-%m"))
            cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 \
                else cur.replace(month=cur.month + 1)
    else:
        cur = _midnight(start)
        while cur < end:
            out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
    return out


@app.get("/api/overview")
def overview(
    period: str = "weekly",
    ref: Optional[str] = None,
    machine: Optional[str] = None,
    source: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Everything one screen needs for a period: the reading, who burned it,
    through which harness and on which model, and how it compares with the
    period before. Periods are local (see _period_bounds)."""
    start, end = _period_bounds(period, ref)
    base = _where(start, end, source, machine)
    days = max((end - start).total_seconds() / 86400, 1e-9)

    def totals_for(filters) -> dict:
        row = session.exec(
            select(
                func.count(UsageTurn.id),
                func.sum(UsageTurn.total_tokens),
                func.sum(UsageTurn.cost_eur),
                func.sum(UsageTurn.cost_usd),
                *[func.sum(getattr(UsageTurn, c)) for c in _TOKEN_COLS],
                func.count(func.distinct(UsageTurn.session_id)),
                func.count(func.distinct(UsageTurn.model_id)),
                func.count(func.distinct(UsageTurn.machine)),
            ).where(*filters)
        ).one()
        turns, tok, eur, usd = row[0], row[1], row[2], row[3]
        split = dict(zip(_TOKEN_COLS, (int(v or 0) for v in row[4:4 + len(_TOKEN_COLS)])))
        return {
            "turns": int(turns or 0),
            "tokens": int(tok or 0),
            "cost_eur": round(eur or 0, 4),
            "cost_usd": round(usd or 0, 4),
            **split,
            "sessions": int(row[-3] or 0),
            "models": int(row[-2] or 0),
            "machines": int(row[-1] or 0),
        }

    totals = totals_for(base)

    # Subscriptions are billed monthly; a week's share is pro-rated by days so
    # "paid" can be compared with "list price" over any period.
    year_month = _from_utc(start).strftime("%Y-%m")
    month_fee = {t: pricing.get_sub_eur(session, t, year_month) for t in TOOLS}
    fee_factor = days / 30.4375
    sub_for = lambda t: round(month_fee.get(t, 0.0) * fee_factor, 2)
    totals["sub_eur"] = round(sum(sub_for(t) for t in (TOOLS if not source else [source])), 2)
    totals["sub_eur_month"] = round(sum(month_fee[t] for t in (TOOLS if not source else [source])), 2)

    prev_start, prev_end = _previous_bounds(period, start)
    previous = None
    if prev_start:
        previous = totals_for(_where(prev_start, prev_end, source, machine))
        previous["from"], previous["to"] = prev_start.isoformat(), prev_end.isoformat()

    def group(col, key, empty, extra=None):
        rows = session.exec(
            select(
                col,
                func.count(UsageTurn.id),
                func.sum(UsageTurn.total_tokens),
                func.sum(UsageTurn.cost_eur),
                *[func.sum(getattr(UsageTurn, c)) for c in _TOKEN_COLS],
                func.count(func.distinct(UsageTurn.model_id)),
            ).where(*base).group_by(col)
        ).all()
        out = []
        for r in rows:
            item = {
                key: r[0] or empty,
                "turns": int(r[1] or 0),
                "tokens": int(r[2] or 0),
                "cost_eur": round(r[3] or 0, 4),
                **dict(zip(_TOKEN_COLS, (int(v or 0) for v in r[4:4 + len(_TOKEN_COLS)]))),
                "models": int(r[-1] or 0),
            }
            if extra:
                extra(item)
            out.append(item)
        return sorted(out, key=lambda x: -x["tokens"])

    def with_sub(item):
        item["sub_eur"] = sub_for(item["source"])
        item["sub_eur_month"] = month_fee.get(item["source"], 0.0)

    by_source = group(UsageTurn.source, "source", "(unknown)", with_sub)
    for tool in TOOLS:                       # a harness with no usage still has a fee
        if source in (None, tool) and not any(r["source"] == tool for r in by_source):
            by_source.append({"source": tool, "turns": 0, "tokens": 0, "cost_eur": 0.0,
                              **{c: 0 for c in _TOKEN_COLS}, "models": 0,
                              "sub_eur": sub_for(tool), "sub_eur_month": month_fee.get(tool, 0.0)})

    machines_seen = {m.name: m for m in session.exec(select(Machine)).all()}

    # What each machine worked in, and which tools ran there: the machine screen
    # answers "533M on what?" without a second request.
    def per_machine(col, key, empty, limit):
        out: dict = {}
        for mach, value, tok, n, eur in session.exec(
            select(UsageTurn.machine, col, func.sum(UsageTurn.total_tokens), func.count(UsageTurn.id),
                   func.sum(UsageTurn.cost_eur))
            .where(*base).group_by(UsageTurn.machine, col)
        ).all():
            out.setdefault(mach, []).append({
                key: value or empty, "tokens": int(tok or 0), "turns": int(n),
                "cost_eur": round(eur or 0, 4),
            })
        for rows in out.values():
            rows.sort(key=lambda r: -r["tokens"])
            del rows[limit:]
        return out

    machine_projects = per_machine(UsageTurn.project, "project", "(no folder)", 5)
    machine_sources = per_machine(UsageTurn.source, "source", "(unknown)", 6)

    def with_machine_meta(item):
        m = machines_seen.get(item["machine"])
        item["last_seen_at"] = m.last_seen_at.isoformat() if m and m.last_seen_at else None
        item["registered"] = bool(m)
        item["projects"] = machine_projects.get(item["machine"], [])
        item["sources"] = machine_sources.get(item["machine"], [])

    by_machine = group(UsageTurn.machine, "machine", "(unknown)", with_machine_meta)
    for name, m in machines_seen.items():    # registered but silent this period
        if not any(r["machine"] == name for r in by_machine):
            by_machine.append({"machine": name, "turns": 0, "tokens": 0, "cost_eur": 0.0,
                               **{c: 0 for c in _TOKEN_COLS}, "models": 0, "registered": True,
                               "projects": [], "sources": [],
                               "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None})

    by_model = group(UsageTurn.model_id, "model_id", "(unknown)")[:14]

    # Which harnesses worked in each folder, so a folder can carry the colour of
    # the one that did most of the work there (colour is harness identity, always).
    project_sources: dict = {}
    for proj, src, tok, n, eur in session.exec(
        select(UsageTurn.project, UsageTurn.source, func.sum(UsageTurn.total_tokens),
               func.count(UsageTurn.id), func.sum(UsageTurn.cost_eur))
        .where(*base).group_by(UsageTurn.project, UsageTurn.source)
    ).all():
        project_sources.setdefault(proj or "(no folder)", []).append({
            "source": src or "(unknown)", "tokens": int(tok or 0), "turns": int(n),
            "cost_eur": round(eur or 0, 4),
        })
    for slices in project_sources.values():
        slices.sort(key=lambda r: -r["tokens"])
        del slices[4:]

    # The branches worked on in each folder: roughly, what each project's
    # tokens were spent on (one branch is usually one pull request).
    project_branches: dict = {}
    for proj, branch, tok, n, eur in session.exec(
        select(UsageTurn.project, UsageTurn.branch, func.sum(UsageTurn.total_tokens),
               func.count(UsageTurn.id), func.sum(UsageTurn.cost_eur))
        .where(*base, UsageTurn.branch.is_not(None)).group_by(UsageTurn.project, UsageTurn.branch)
    ).all():
        project_branches.setdefault(proj or "(no folder)", []).append({
            "branch": branch, "tokens": int(tok or 0), "turns": int(n), "cost_eur": round(eur or 0, 4),
        })
    for slices in project_branches.values():
        slices.sort(key=lambda r: -r["tokens"])
        del slices[5:]

    def with_sources(item):
        item["sources"] = project_sources.get(item["project"], [])
        item["branches"] = project_branches.get(item["project"], [])

    by_project = group(UsageTurn.project, "project", "(no folder)", with_sources)[:20]
    by_origin = group(UsageTurn.origin, "origin", "(unrecorded)")

    # Which harness each model ran under, so a model row can carry its colour.
    model_source = dict(session.exec(
        select(UsageTurn.model_id, func.min(UsageTurn.source)).where(*base).group_by(UsageTurn.model_id)
    ).all())
    for row in by_model:
        row["source"] = model_source.get(row["model_id"], "(unknown)")

    # Series, bucketed by local day (month over a year).
    hour = func.strftime("%Y-%m-%d %H", UsageTurn.ts)
    series = {b: {"bucket": b, "tokens": 0, "turns": 0, "cost_eur": 0.0,
                  "by_source": {}, "by_machine": {}, "by_model": {},
                  "cost_by_source": {}, "cost_by_machine": {}, "cost_by_model": {}}
              for b in _buckets_for(period, start, end)}
    for h, src, mach, n, tok, eur in session.exec(
        select(hour, UsageTurn.source, UsageTurn.machine, func.count(UsageTurn.id),
               func.sum(UsageTurn.total_tokens), func.sum(UsageTurn.cost_eur))
        .where(*base).group_by(hour, UsageTurn.source, UsageTurn.machine)
    ).all():
        b = _bucket_key(h, period)
        e = series.get(b)
        if e is None:                        # a row on the boundary of a DST shift
            continue
        tok, n = int(tok or 0), int(n or 0)
        e["tokens"] += tok
        e["turns"] += n
        e["cost_eur"] = round(e["cost_eur"] + (eur or 0), 4)
        e["by_source"][src] = e["by_source"].get(src, 0) + tok
        e["by_machine"][mach] = e["by_machine"].get(mach, 0) + tok
        # The same buckets in money, so the daily bars can switch measure
        # without asking the hub again.
        eur = round(eur or 0, 4)
        e["cost_by_source"][src] = round(e["cost_by_source"].get(src, 0) + eur, 4)
        e["cost_by_machine"][mach] = round(e["cost_by_machine"].get(mach, 0) + eur, 4)

    # The models behind each bucket, so the chart's readout can name them.
    for h, model, tok, eur in session.exec(
        select(hour, UsageTurn.model_id, func.sum(UsageTurn.total_tokens), func.sum(UsageTurn.cost_eur))
        .where(*base).group_by(hour, UsageTurn.model_id)
    ).all():
        e = series.get(_bucket_key(h, period))
        if e is None:
            continue
        name = model or "(unknown)"
        e["by_model"][name] = e["by_model"].get(name, 0) + int(tok or 0)
        e["cost_by_model"][name] = round(e["cost_by_model"].get(name, 0) + (eur or 0), 4)

    series = [series[b] for b in sorted(series)]
    busiest = max(series, key=lambda b: b["tokens"], default=None)

    return {
        "period": period, "ref": ref, "machine": machine, "source": source,
        "tz": str(LOCAL_TZ),
        "from": start.isoformat(), "to": end.isoformat(),
        "from_local": _from_utc(start).isoformat(), "to_local": _from_utc(end).isoformat(),
        "totals": totals,
        "previous": previous,
        "by_source": by_source,
        "by_machine": by_machine,
        "by_model": by_model,
        "by_project": by_project,
        "by_origin": by_origin,
        "series": series,
        "busiest": busiest if busiest and busiest["tokens"] else None,
    }


# ── prices ────────────────────────────────────────────────────────────────────

@app.get("/api/prices")
def list_prices(session: Session = Depends(get_session)):
    rows = session.exec(select(ModelPrice)).all()
    return [{
        "model_id": r.model_id,
        "display_name": r.display_name,
        "provider": r.provider,
        "prompt": r.prompt,
        "completion": r.completion,
        "cache_read": r.cache_read,
        "cache_write": r.cache_write,
        "reasoning": r.reasoning,
        "source": r.source,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]


@app.post("/api/prices/mirror-pull")
async def mirror_pull_prices(session: Session = Depends(get_session)):
    """Pull the latest rate card from the central model-prices service and
    recompute every historical turn's cost against it. Called by the weekly
    launchd cron (Monday 05:00); prices are edited in model-prices, not here."""
    result = await pricing.mirror_pull_prices(session)
    if result["synced"]:
        result["rows_recomputed"] = ingest.recompute_costs()
    return result


# ── settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(session: Session = Depends(get_session)):
    out = {"fx_rate": pricing.get_fx_rate(session)}
    for key in sorted(SETTING_KEYS - {"fx_rate"}):
        out[key] = pricing.get_sub_eur(session, key[len("sub_"):-len("_eur")])
    return out


@app.patch("/api/settings")
def update_settings(payload: dict, session: Session = Depends(get_session)):
    for k, v in payload.items():
        if k in SETTING_KEYS:
            row = session.get(Setting, k) or Setting(key=k)
            row.value = str(v)
            session.add(row)
    session.commit()
    return get_settings(session)


# ── manual bills (per-month variable subscriptions) ───────────────────────────

@app.get("/api/bills/{source}")
def list_bills(source: str, session: Session = Depends(get_session)):
    bills = session.exec(select(ManualBill).where(ManualBill.source == source)).all()
    return sorted(
        [{"year_month": b.year_month, "amount_eur": b.amount_eur} for b in bills],
        key=lambda b: b["year_month"],
    )


@app.patch("/api/bills/{source}/{year_month}")
def patch_bill(source: str, year_month: str, payload: dict, session: Session = Depends(get_session)):
    bill = session.get(ManualBill, (source, year_month)) or ManualBill(source=source, year_month=year_month)
    if "amount_eur" in payload:
        bill.amount_eur = float(payload["amount_eur"])
    session.add(bill)
    session.commit()
    return {"source": bill.source, "year_month": bill.year_month, "amount_eur": bill.amount_eur}
