"""Price resolution for Meterlex.

The rate card itself lives in the model-prices service (webapps/model-prices)
and is mirrored into the local model_prices table — see mirror_pull_prices()
below. This module keeps the resolve/fallback logic, which is Meterlex-specific
(e.g. Ollama routing, the copilot-auto stand-in, dated-variant fallbacks) and
doesn't belong in the shared service.
"""
import logging
import os
from datetime import date, datetime
from typing import Optional

import httpx
from sqlmodel import Session

from models import ModelPrice, PricePoint, Setting

log = logging.getLogger("meterlex.pricing")

MODEL_PRICES_URL = os.getenv("MODEL_PRICES_URL", "http://host.docker.internal:8693")

FREE_MODEL_PREFIXES = ("<synthetic>", "local/")


def _is_free(model_id: str) -> bool:
    return any(model_id.startswith(p) or model_id == p.rstrip("-")
               for p in FREE_MODEL_PREFIXES) or model_id in {"synthetic", "<synthetic>"}


def _card(rows: list, points: list) -> set:
    """What a rate card says, its price history included, without the pull's own timestamp."""
    return ({(r.model_id, r.display_name, r.provider, r.prompt, r.completion,
              r.cache_read, r.cache_write, r.reasoning, r.source) for r in rows}
            | {("point", p.model_id, p.on, p.prompt, p.completion, p.cache_read, p.cache_write)
               for p in points})


async def mirror_pull_prices(session: Session) -> dict:
    """Pull the shared rate card from the central model-prices service and
    replace the local mirror with it. Never raises — on failure, logs and
    keeps whatever is already in the local table (an outage here must not
    zero out every turn's cost)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{MODEL_PRICES_URL}/api/prices")
            resp.raise_for_status()
            rows = resp.json()
    except Exception as exc:
        log.warning("model-prices mirror pull failed: %s", exc)
        return {"synced": False, "error": str(exc), "count": 0}

    if not rows:
        log.warning("model-prices returned 0 rows — keeping existing mirror")
        return {"synced": False, "error": "empty response", "count": 0}

    from sqlmodel import select
    incoming, points = [], []
    for r in rows:
        row = ModelPrice(model_id=r["model_id"])
        row.display_name = r.get("display_name")
        row.provider = r.get("provider")
        row.prompt = r.get("prompt", 0.0)
        row.completion = r.get("completion", 0.0)
        row.cache_read = r.get("cache_read", 0.0)
        row.cache_write = r.get("cache_write", 0.0)
        row.reasoning = r.get("reasoning", 0.0)
        row.source = r.get("source", "seed")
        row.updated_at = datetime.utcnow()
        incoming.append(row)
        for p in r.get("history") or []:
            try:
                on = date.fromisoformat(p["on"])
            except (KeyError, TypeError, ValueError):
                continue
            points.append(PricePoint(
                model_id=row.model_id, on=on, prompt=p.get("prompt", 0.0), completion=p.get("completion", 0.0),
                cache_read=p.get("cache_read", 0.0), cache_write=p.get("cache_write", 0.0),
            ))

    existing_rows = session.exec(select(ModelPrice)).all()
    existing_points = session.exec(select(PricePoint)).all()
    changed = _card(existing_rows, existing_points) != _card(incoming, points)
    for existing in [*existing_rows, *existing_points]:
        session.delete(existing)
    session.flush()
    session.add_all(incoming)
    session.add_all(points)
    session.info.pop(_POINTS_KEY, None)
    n = len(incoming)
    session.commit()
    row = session.get(Setting, "last_price_mirror_pull") or Setting(key="last_price_mirror_pull", value="")
    row.value = datetime.utcnow().isoformat()
    session.add(row)
    session.commit()
    return {"synced": True, "changed": changed, "count": n, "error": None}


def ollama_cloud_id(model_id: str) -> str:
    """model-prices keys Ollama Cloud rows as `ollama/<model>:cloud`."""
    m = model_id.split("/", 1)[1] if model_id.startswith("ollama/") else model_id
    for suffix in (":cloud", "-cloud"):
        if m.endswith(suffix):
            m = m[: -len(suffix)]
    return f"ollama/{m}:cloud"


def resolve_price(session: Session, model_id: str, source: Optional[str] = None) -> Optional[ModelPrice]:
    if source == "ollama" or model_id.startswith("ollama/"):
        # A model Ollama sells in its cloud is billed at Ollama's own rate;
        # anything else Ollama served ran on this machine, for free.
        row = session.get(ModelPrice, ollama_cloud_id(model_id))
        if row or not model_id.endswith((":cloud", "-cloud")):
            return row
    row = session.get(ModelPrice, model_id)
    if row:
        return row
    m = model_id.lower()
    # Generic Gemini fallback for unrecognised gemini-* model IDs
    if m.startswith("gemini"):
        return session.get(ModelPrice, "gemini")
    # Generic GLM fallback for unrecognised glm-* IDs (e.g. dated variants)
    if m.startswith("glm-"):
        return session.get(ModelPrice, "glm-5.2")
    # Generic Codex fallback for unrecognised gpt-5* IDs (new dated variants)
    if m.startswith("gpt-5"):
        return session.get(ModelPrice, "gpt-5")
    # Claude Code names Haiku 4.5 with or without its date
    if m.startswith("claude-haiku-4-5"):
        return session.get(ModelPrice, "claude-haiku-4-5-20251001")
    # Copilot CLI: a model with no rate of its own gets the representative one
    if source == "copilot":
        return session.get(ModelPrice, "copilot-auto")
    return None


_POINTS_KEY = "meterlex_price_points"


def _points(session: Session) -> dict:
    """Every model's dated rates, oldest first, read once per session: a
    reprice walks thousands of turns through the same one."""
    cached = session.info.get(_POINTS_KEY)
    if cached is None:
        from sqlmodel import select
        cached = {}
        for p in session.exec(select(PricePoint).order_by(PricePoint.on)).all():
            cached.setdefault(p.model_id, []).append(
                (p.on, p.prompt, p.completion, p.cache_read, p.cache_write))
        session.info[_POINTS_KEY] = cached
    return cached


def rates_on(session: Session, row: ModelPrice, at: Optional[datetime]):
    """The row's rates in force on `at`'s day: (prompt, completion, cache_read, cache_write, reasoning).

    The newest point is today's rate, so the row itself applies from that
    day on (and with no date or no history). A turn older than the first
    point takes the first, the launch price."""
    current = (row.prompt, row.completion, row.cache_read, row.cache_write, row.reasoning)
    pts = _points(session).get(row.model_id) if at else None
    if not pts:
        return current
    day = at.date()
    i = max((n for n, p in enumerate(pts) if p[0] <= day), default=0)
    if i == len(pts) - 1:
        return current
    on, prompt, completion, cache_read, cache_write = pts[i]
    # Reasoning tokens bill at the output rate when the card says so; follow it back in time.
    reasoning = completion if row.reasoning == row.completion else row.reasoning
    return (prompt, completion, cache_read, cache_write, reasoning)


def compute_cost(session: Session, provider: str, model_id: str, usage: dict, source: Optional[str] = None,
                 at: Optional[datetime] = None):
    """Returns (cost_usd, source), at the rates in force on `at`'s day when given."""
    if _is_free(model_id):
        return (0.0, "free")

    row = resolve_price(session, model_id, source)
    if row is None:
        return (0.0, "free")
    prompt, completion, cache_read, cache_write, reasoning = rates_on(session, row, at)

    in_t = int(usage.get("input", 0) or 0)
    out_t = int(usage.get("output", 0) or 0)
    ca_r = int(usage.get("cacheRead", 0) or 0)
    ca_w = int(usage.get("cacheWrite", 0) or 0)
    rsn = int(usage.get("reasoningTokens", 0) or 0)

    cost = (
        in_t * prompt
        + out_t * completion
        + ca_r * cache_read
        + ca_w * cache_write
        + rsn * reasoning
    )
    return (round(cost, 8), row.source)


def get_fx_rate(session: Session) -> float:
    row = session.get(Setting, "fx_rate")
    try:
        return float(row.value) if row else 0.92
    except ValueError:
        return 0.92


def get_sub_eur(session: Session, tool: str, year_month: Optional[str] = None) -> float:
    """Return subscription cost for tool. For copilot, prefers ManualBill for the given month."""
    if tool == "copilot" and year_month:
        from models import ManualBill
        bill = session.get(ManualBill, ("copilot", year_month))
        if bill:
            return bill.amount_eur
    row = session.get(Setting, f"sub_{tool.replace('-', '_')}_eur")
    try:
        return float(row.value) if row else 0.0
    except ValueError:
        return 0.0
