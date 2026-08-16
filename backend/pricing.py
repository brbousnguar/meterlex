"""Price resolution for Meterlex.

The rate card itself now lives in the model-prices service (webapps/model-prices)
and is mirrored into the local model_prices table — see mirror_pull_prices()
below. This module keeps the resolve/fallback logic, which is Meterlex-specific
(e.g. the copilot-auto stand-in, dated-variant fallbacks) and doesn't belong
in the shared service.
"""
import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from sqlmodel import Session

from models import ModelPrice, Setting

log = logging.getLogger("meterlex.pricing")

MODEL_PRICES_URL = os.getenv("MODEL_PRICES_URL", "http://host.docker.internal:8693")

FREE_MODEL_PREFIXES = ("<synthetic>", "local/")


def _is_free(model_id: str) -> bool:
    return any(model_id.startswith(p) or model_id == p.rstrip("-")
               for p in FREE_MODEL_PREFIXES) or model_id in {"synthetic", "<synthetic>"}


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
    for existing in session.exec(select(ModelPrice)).all():
        session.delete(existing)

    n = 0
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
        session.add(row)
        n += 1
    session.commit()
    row = session.get(Setting, "last_price_mirror_pull") or Setting(key="last_price_mirror_pull", value="")
    row.value = datetime.utcnow().isoformat()
    session.add(row)
    session.commit()
    return {"synced": True, "count": n, "error": None}


def resolve_price(session: Session, model_id: str) -> Optional[ModelPrice]:
    row = session.get(ModelPrice, model_id)
    if row:
        return row
    # Generic Gemini fallback for unrecognised gemini-* model IDs
    if model_id.lower().startswith("gemini"):
        return session.get(ModelPrice, "gemini")
    # Generic GLM fallback for unrecognised glm-* IDs (e.g. dated variants)
    if model_id.lower().startswith("glm-"):
        return session.get(ModelPrice, "glm-5.2")
    # Generic Codex fallback for unrecognised gpt-5* IDs (new dated variants)
    if model_id.lower().startswith("gpt-5"):
        return session.get(ModelPrice, "gpt-5")
    return None


def compute_cost(session: Session, provider: str, model_id: str, usage: dict):
    """Returns (cost_usd, source)."""
    if _is_free(model_id):
        return (0.0, "free")

    row = resolve_price(session, model_id)
    if row is None:
        return (0.0, "free")

    in_t = int(usage.get("input", 0) or 0)
    out_t = int(usage.get("output", 0) or 0)
    ca_r = int(usage.get("cacheRead", 0) or 0)
    ca_w = int(usage.get("cacheWrite", 0) or 0)
    rsn = int(usage.get("reasoningTokens", 0) or 0)

    cost = (
        in_t * row.prompt
        + out_t * row.completion
        + ca_r * row.cache_read
        + ca_w * row.cache_write
        + rsn * row.reasoning
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
