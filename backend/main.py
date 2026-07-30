"""Agentic Spend API — tracks Claude Code, Codex, and Antigravity usage.

Ports: API 8692, UI 5180.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, func

from database import engine, create_db, get_session
from models import UsageTurn, ModelPrice, Setting, ManualBill
import pricing
import ingest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("agentic-spend")

TOOLS = ["claude-code", "codex", "antigravity", "ollama", "copilot"]

DEFAULT_SETTINGS = {
    "fx_rate": "0.92",
    "sub_claude_code_eur": "21.60",
    "sub_codex_eur": "23.00",
    "sub_antigravity_eur": "18.33",
    "sub_ollama_eur": "18.18",
    "sub_copilot_eur": "0.00",
}

# Copilot CLI on this machine is logged into the SQLI work seat
# (brbousnguar_SQLI), not the personal brbousnguar subscription — so the
# personal cost is €0 here. We still seed per-month bills (at €0) so the
# variable-charge tracking stays in place, but savings reduce to the
# consumption-equivalent API cost — the number that steers real usage.
COPILOT_HISTORY = {
    "2026-01": 0.0,
    "2026-02": 0.0,
    "2026-03": 0.0,
    "2026-04": 0.0,
    "2026-07": 0.0,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()
    with Session(engine) as session:
        for k, v in DEFAULT_SETTINGS.items():
            if not session.get(Setting, k):
                session.add(Setting(key=k, value=v))
        # Seed Copilot billing history (idempotent)
        for ym, amt in COPILOT_HISTORY.items():
            if not session.get(ManualBill, ("copilot", ym)):
                session.add(ManualBill(source="copilot", year_month=ym, amount_eur=amt))
        # Migrate GLM/local turns from claude-code → ollama
        from sqlalchemy import text
        session.execute(text(
            "UPDATE usage_turns SET source='ollama' "
            "WHERE source='claude-code' AND (model_id LIKE 'glm-%' OR model_id LIKE 'local/%')"
        ))
        # One-time: this machine's Copilot CLI runs under the SQLI work seat,
        # not the paid personal subscription, so zero any previously-seeded
        # Copilot bills. Guarded so it runs once and never clobbers future
        # manual edits made via the UI.
        if not session.get(Setting, "migrated_copilot_zero_v2"):
            for ym, _ in COPILOT_HISTORY.items():
                bill = session.get(ManualBill, ("copilot", ym))
                if bill and bill.amount_eur != 0.0:
                    bill.amount_eur = 0.0
                    session.add(bill)
            sub_row = session.get(Setting, "sub_copilot_eur")
            if sub_row and sub_row.value != "0.00":
                sub_row.value = "0.00"
                session.add(sub_row)
            session.add(Setting(key="migrated_copilot_zero_v2", value="1"))
        # One-time: earlier Codex ingestion hardcoded every paid turn's
        # model_id to "gpt-5" instead of reading the real per-turn model
        # from turn_context.model (gpt-5.4, gpt-5.5, gpt-5.6-sol, etc).
        # v2: the first fix tracked the model in-memory per scan but didn't
        # persist it across incremental (60s) scans, so turns ingested
        # between deploys still got mistagged "gpt-5". Purge again now that
        # ScanState.last_model carries the model across incremental scans.
        # Startup always runs a full rescan (offset 0), so this is safe.
        if not session.get(Setting, "migrated_codex_model_reimport_v2"):
            session.execute(text("DELETE FROM usage_turns WHERE source='codex'"))
            session.add(Setting(key="migrated_codex_model_reimport_v2", value="1"))
        session.commit()
    task = asyncio.create_task(ingest.ingest_loop())
    log.info("ingest loop started")
    yield
    task.cancel()


app = FastAPI(title="Agentic Spend API", version="0.1.0", lifespan=lifespan)
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
            y, m = map(int, ref.split("-"))
            start = datetime(y, m, 1)
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
        y = int(ref) if ref else now.year
        return datetime(y, 1, 1), datetime(y + 1, 1, 1)
    raise HTTPException(400, "period must be monthly, daily, or yearly")


# ── health / control ──────────────────────────────────────────────────────────

@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    total = session.exec(select(func.count(UsageTurn.id))).one()
    last = session.exec(select(func.max(UsageTurn.created_at))).one()
    by_source = session.exec(
        select(UsageTurn.source, func.count(UsageTurn.id)).group_by(UsageTurn.source)
    ).all()
    return {
        "status": "ok",
        "total_turns": total,
        "last_ingested_at": last.isoformat() if last else None,
        "by_source": {s: n for s, n in by_source},
    }


@app.post("/api/scan")
def api_scan(full: bool = False):
    return ingest.trigger_scan(full=full)


@app.post("/api/recompute")
def api_recompute():
    return {"rows_updated": ingest.recompute_costs()}


# ── summary / spend ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary(
    period: str = "monthly",
    ref: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Per-tool cost summary + subscription comparison."""
    start, end = _period_bounds(period, ref)
    base = [UsageTurn.ts >= start, UsageTurn.ts < end]

    rows = session.exec(
        select(
            UsageTurn.source,
            func.count(UsageTurn.id),
            func.sum(UsageTurn.cost_eur),
            func.sum(UsageTurn.input_tokens),
            func.sum(UsageTurn.output_tokens),
            func.sum(UsageTurn.total_tokens),
        ).where(*base).group_by(UsageTurn.source)
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
        "period": period, "ref": ref,
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

    base = [UsageTurn.ts >= start, UsageTurn.ts < end]
    if source:
        base.append(UsageTurn.source == source)

    total_eur = session.exec(select(func.sum(UsageTurn.cost_eur)).where(*base)).one() or 0.0
    total_usd = session.exec(select(func.sum(UsageTurn.cost_usd)).where(*base)).one() or 0.0
    turns = session.exec(select(func.count(UsageTurn.id)).where(*base)).one() or 0

    by_model = session.exec(
        select(
            UsageTurn.model_id,
            func.sum(UsageTurn.cost_eur),
            func.count(UsageTurn.id),
            func.sum(UsageTurn.input_tokens),
            func.sum(UsageTurn.output_tokens),
            func.sum(UsageTurn.total_tokens),
        ).where(*base).group_by(UsageTurn.model_id)
    ).all()

    by_project = session.exec(
        select(
            UsageTurn.project,
            func.sum(UsageTurn.cost_eur),
            func.count(UsageTurn.id),
        ).where(*base).group_by(UsageTurn.project)
    ).all()

    year_month = start.strftime("%Y-%m")
    sub = pricing.get_sub_eur(session, source, year_month) if source else sum(
        pricing.get_sub_eur(session, t, year_month) for t in TOOLS
    )
    return {
        "source": source,
        "from": start.isoformat(), "to": end.isoformat(),
        "total_usd": round(total_usd, 4),
        "total_eur": round(total_eur, 4),
        "sub_eur": sub,
        "turns": int(turns),
        "by_model": sorted(
            [{"model_id": m, "cost_eur": round(c or 0, 4), "turns": int(n),
              "input_tokens": int(inp or 0), "output_tokens": int(out or 0),
              "total_tokens": int(tok or 0)}
             for m, c, n, inp, out, tok in by_model],
            key=lambda x: -x["cost_eur"],
        ),
        "by_project": sorted(
            [{"project": p or "(no cwd)", "cost_eur": round(c or 0, 4), "turns": int(n)}
             for p, c, n in by_project],
            key=lambda x: -x["cost_eur"],
        ),
    }


@app.get("/api/spend/timeseries")
def spend_timeseries(
    period: str = "monthly",
    source: Optional[str] = None,
    ref: Optional[str] = None,
    session: Session = Depends(get_session),
):
    start, end = _period_bounds(period, ref)
    fmt = "%Y-%m" if period == "yearly" else "%Y-%m-%d"
    base = [UsageTurn.ts >= start, UsageTurn.ts < end]
    if source:
        base.append(UsageTurn.source == source)

    bucket_expr = func.strftime(fmt, UsageTurn.ts)
    rows = session.exec(
        select(
            bucket_expr,
            UsageTurn.source,
            func.sum(UsageTurn.cost_eur),
            func.count(UsageTurn.id),
            func.sum(UsageTurn.total_tokens),
        ).where(*base).group_by(bucket_expr, UsageTurn.source)
    ).all()

    # Per-bucket by-model split (for the "spend by model" chart view).
    model_rows = session.exec(
        select(
            bucket_expr,
            UsageTurn.model_id,
            func.sum(UsageTurn.cost_eur),
            func.sum(UsageTurn.total_tokens),
        ).where(*base).group_by(bucket_expr, UsageTurn.model_id)
    ).all()

    buckets = {}
    for b, src, cost, n, tok in rows:
        entry = buckets.setdefault(b, {"bucket": b, "cost_eur": 0.0, "turns": 0,
                                       "by_source": {}, "tokens_by_source": {},
                                       "by_model": {}, "tokens_by_model": {}})
        cost = round(cost or 0, 4)
        entry["cost_eur"] = round(entry["cost_eur"] + cost, 4)
        entry["turns"] += int(n)
        entry["by_source"][src] = round(entry["by_source"].get(src, 0) + cost, 4)
        entry["tokens_by_source"][src] = entry["tokens_by_source"].get(src, 0) + int(tok or 0)

    for b, mid, cost, tok in model_rows:
        entry = buckets.setdefault(b, {"bucket": b, "cost_eur": 0.0, "turns": 0,
                                       "by_source": {}, "tokens_by_source": {},
                                       "by_model": {}, "tokens_by_model": {}})
        mid = mid or "(unknown)"
        entry["by_model"][mid] = round(entry["by_model"].get(mid, 0) + (cost or 0), 4)
        entry["tokens_by_model"][mid] = entry["tokens_by_model"].get(mid, 0) + int(tok or 0)

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


@app.patch("/api/prices/{model_id:path}")
def update_price(model_id: str, payload: dict, session: Session = Depends(get_session)):
    mid = unquote(model_id)
    row = session.get(ModelPrice, mid) or ModelPrice(model_id=mid)
    for k in ("display_name", "provider", "prompt", "completion", "cache_read", "cache_write", "reasoning"):
        if k in payload:
            setattr(row, k, payload[k])
    row.source = "manual"
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return {"model_id": row.model_id, "source": row.source}


# ── settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(session: Session = Depends(get_session)):
    return {
        "fx_rate": pricing.get_fx_rate(session),
        "sub_claude_code_eur": pricing.get_sub_eur(session, "claude-code"),
        "sub_codex_eur": pricing.get_sub_eur(session, "codex"),
        "sub_antigravity_eur": pricing.get_sub_eur(session, "antigravity"),
        "sub_ollama_eur": pricing.get_sub_eur(session, "ollama"),
        "sub_copilot_eur": pricing.get_sub_eur(session, "copilot"),
    }


@app.patch("/api/settings")
def update_settings(payload: dict, session: Session = Depends(get_session)):
    allowed = {
        "fx_rate", "sub_claude_code_eur", "sub_codex_eur",
        "sub_antigravity_eur", "sub_ollama_eur", "sub_copilot_eur",
    }
    for k, v in payload.items():
        if k in allowed:
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
