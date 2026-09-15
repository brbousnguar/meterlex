"""Meterlex API — AI coding usage from every machine, priced.

Collectors on each machine POST their usage to /api/ingest with the machine's
key; everything else is read-only views over the stored turns.
Ports: API 8692, UI 5180 (both bound to 127.0.0.1; reach the UI over Tailscale).
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
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

TOOLS = ["claude-code", "codex", "antigravity", "gemini-cli", "ollama", "copilot"]

DEFAULT_SETTINGS = {
    "fx_rate": "0.92",
    "sub_claude_code_eur": "21.60",
    "sub_codex_eur": "23.00",
    "sub_antigravity_eur": "18.33",
    "sub_gemini_cli_eur": "0.00",
    "sub_ollama_eur": "18.18",
    "sub_copilot_eur": "0.00",
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


def _period_bounds(period: str, ref: Optional[str]):
    now = datetime.utcnow()
    if period == "monthly":
        if ref:
            try:
                y, m = map(int, ref.split("-"))
                start = datetime(y, m, 1)
            except (TypeError, ValueError):
                raise HTTPException(400, "monthly ref must use YYYY-MM")
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(month=start.month % 12 + 1) if start.month < 12 \
            else start.replace(year=start.year + 1, month=1)
        return start, end
    elif period == "daily":
        if ref:
            start = _parse_date(ref)
            return start, start + timedelta(days=1)
        return now - timedelta(days=30), now
    elif period == "yearly":
        try:
            y = int(ref) if ref else now.year
        except (TypeError, ValueError):
            raise HTTPException(400, "yearly ref must use YYYY")
        return datetime(y, 1, 1), datetime(y + 1, 1, 1)
    raise HTTPException(400, "period must be monthly, daily, or yearly")


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
