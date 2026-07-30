"""Price resolution for agentic-spend.

Prices are seeded manually from public Anthropic/OpenAI rate cards.
No OpenRouter sync needed — we only track a small fixed set of models.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import Session

from models import ModelPrice, Setting

# USD per single token
SEED_PRICES = {
    "claude-sonnet-4-6": {
        "display_name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
    },
    "claude-opus-4-8": {
        "display_name": "Claude Opus 4.8",
        "provider": "anthropic",
        "prompt": 15e-6, "completion": 75e-6,
        "cache_read": 1.5e-6, "cache_write": 18.75e-6, "reasoning": 0.0,
    },
    "claude-haiku-4-5-20251001": {
        "display_name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "prompt": 0.8e-6, "completion": 4e-6,
        "cache_read": 0.08e-6, "cache_write": 1e-6, "reasoning": 0.0,
    },
    "claude-sonnet-5": {
        "display_name": "Claude Sonnet 5",
        "provider": "anthropic",
        "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
    },
    "claude-fable-5": {
        "display_name": "Claude Fable 5",
        "provider": "anthropic",
        "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
    },
    "gpt-5": {
        "display_name": "GPT-5 (Codex)",
        "provider": "openai",
        "prompt": 5e-6, "completion": 30e-6,
        "cache_read": 0.5e-6, "cache_write": 0.0, "reasoning": 30e-6,
    },
    # Gemini flash-tier fallback (covers antigravity internal model names)
    "gemini": {
        "display_name": "Gemini (flash tier)",
        "provider": "google",
        "prompt": 0.1e-6, "completion": 0.4e-6,
        "cache_read": 0.025e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "gemini-3.6-flash": {
        "display_name": "Gemini 3.6 Flash",
        "provider": "google",
        "prompt": 0.1e-6, "completion": 0.4e-6,
        "cache_read": 0.025e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "gemini-3.5-flash": {
        "display_name": "Gemini 3.5 Flash",
        "provider": "google",
        "prompt": 0.075e-6, "completion": 0.3e-6,
        "cache_read": 0.01875e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "gemini-3.5-flash-low": {
        "display_name": "Gemini 3.5 Flash Low",
        "provider": "google",
        "prompt": 0.075e-6, "completion": 0.3e-6,
        "cache_read": 0.01875e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "gemini-3-flash-medium-a": {
        "display_name": "Gemini 3 Flash Medium",
        "provider": "google",
        "prompt": 0.075e-6, "completion": 0.3e-6,
        "cache_read": 0.01875e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
}

FREE_MODEL_PREFIXES = ("glm-", "<synthetic>", "local/")


def _is_free(model_id: str) -> bool:
    return any(model_id.startswith(p) or model_id == p.rstrip("-")
               for p in FREE_MODEL_PREFIXES) or model_id in {"synthetic", "<synthetic>"}


def seed_prices(session: Session) -> int:
    n = 0
    for key, spec in SEED_PRICES.items():
        existing = session.get(ModelPrice, key)
        if existing and existing.source == "manual":
            continue
        row = existing or ModelPrice(model_id=key)
        row.display_name = spec["display_name"]
        row.provider = spec["provider"]
        row.prompt = spec["prompt"]
        row.completion = spec["completion"]
        row.cache_read = spec["cache_read"]
        row.cache_write = spec["cache_write"]
        row.reasoning = spec.get("reasoning", 0.0)
        row.source = "seed"
        row.updated_at = datetime.utcnow()
        session.add(row)
        n += 1
    session.commit()
    return n


def resolve_price(session: Session, model_id: str) -> Optional[ModelPrice]:
    row = session.get(ModelPrice, model_id)
    if row:
        return row
    # Generic Gemini fallback for unrecognised gemini-* model IDs
    if model_id.lower().startswith("gemini"):
        return session.get(ModelPrice, "gemini")
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
