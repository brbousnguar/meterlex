"""Price resolution for agentic-spend.

Prices are seeded manually from public Anthropic/OpenAI rate cards.
No OpenRouter sync needed — we only track a small fixed set of models.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import Session

from models import ModelPrice, Setting

# USD per single token. Per-1M rates divided by 1,000,000.
# Sources:
#   Anthropic — first-party API rate card (2026-06-24).
#     cache_read ≈ 10% of input; cache_write = 1.25× input (5m TTL).
#   OpenAI     — official pricing page. Reasoning tokens bill at the output rate.
#   Z.AI       — official GLM rate card. No separate cache-write/reasoning tier.
SEED_PRICES = {
    # --- Anthropic Claude ---
    "claude-fable-5": {
        "display_name": "Claude Fable 5",
        "provider": "anthropic",
        "prompt": 10e-6, "completion": 50e-6,
        "cache_read": 1.0e-6, "cache_write": 12.5e-6, "reasoning": 0.0,
    },
    "claude-opus-5": {
        "display_name": "Claude Opus 5",
        "provider": "anthropic",
        "prompt": 5e-6, "completion": 25e-6,
        "cache_read": 0.5e-6, "cache_write": 6.25e-6, "reasoning": 0.0,
    },
    "claude-opus-4-8": {
        "display_name": "Claude Opus 4.8",
        "provider": "anthropic",
        "prompt": 5e-6, "completion": 25e-6,
        "cache_read": 0.5e-6, "cache_write": 6.25e-6, "reasoning": 0.0,
    },
    "claude-opus-4-7": {
        "display_name": "Claude Opus 4.7",
        "provider": "anthropic",
        "prompt": 5e-6, "completion": 25e-6,
        "cache_read": 0.5e-6, "cache_write": 6.25e-6, "reasoning": 0.0,
    },
    "claude-opus-4-6": {
        "display_name": "Claude Opus 4.6",
        "provider": "anthropic",
        "prompt": 5e-6, "completion": 25e-6,
        "cache_read": 0.5e-6, "cache_write": 6.25e-6, "reasoning": 0.0,
    },
    "claude-sonnet-5": {
        "display_name": "Claude Sonnet 5",
        "provider": "anthropic",
        "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
    },
    "claude-sonnet-4-6": {
        "display_name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
    },
    "claude-haiku-4-5-20251001": {
        "display_name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "prompt": 1e-6, "completion": 5e-6,
        "cache_read": 0.1e-6, "cache_write": 1.25e-6, "reasoning": 0.0,
    },
    # --- OpenAI (Codex CLI, per turn_context.model) ---
    "gpt-5": {
        "display_name": "GPT-5 (Codex)",
        "provider": "openai",
        "prompt": 1.25e-6, "completion": 10e-6,
        "cache_read": 0.125e-6, "cache_write": 1.25e-6, "reasoning": 10e-6,
    },
    "gpt-5.4": {
        "display_name": "GPT-5.4 (Codex)",
        "provider": "openai",
        "prompt": 1.25e-6, "completion": 10e-6,
        "cache_read": 0.125e-6, "cache_write": 1.25e-6, "reasoning": 10e-6,
    },
    "gpt-5.4-mini": {
        "display_name": "GPT-5.4 Mini (Codex)",
        "provider": "openai",
        "prompt": 0.25e-6, "completion": 2e-6,
        "cache_read": 0.025e-6, "cache_write": 0.25e-6, "reasoning": 2e-6,
    },
    "gpt-5.5": {
        "display_name": "GPT-5.5 (Codex)",
        "provider": "openai",
        "prompt": 1.25e-6, "completion": 10e-6,
        "cache_read": 0.125e-6, "cache_write": 1.25e-6, "reasoning": 10e-6,
    },
    "gpt-5.6-sol": {
        "display_name": "GPT-5.6 Sol (Codex)",
        "provider": "openai",
        "prompt": 1.25e-6, "completion": 10e-6,
        "cache_read": 0.125e-6, "cache_write": 1.25e-6, "reasoning": 10e-6,
    },
    # Representative rate for GitHub Copilot CLI sessions. The agentic CLI
    # records per-session token totals but only exposes `model = 'auto'`
    # (GitHub routes to an undisclosed backend model), so there is no concrete
    # rate card to map to. We price at GPT-5-class rates as a stand-in for the
    # consumption-equivalent cost of a flat Copilot subscription. Override in
    # Prices & settings if you prefer a different representative rate.
    "copilot-auto": {
        "display_name": "GitHub Copilot CLI (auto)",
        "provider": "github",
        "prompt": 1.25e-6, "completion": 10e-6,
        "cache_read": 0.125e-6, "cache_write": 0.0, "reasoning": 10e-6,
    },
    # --- Z.AI GLM (cloud models routed through Claude Code / Ollama) ---
    "glm-5.2": {
        "display_name": "GLM-5.2 (Z.AI)",
        "provider": "zhipu",
        "prompt": 1.4e-6, "completion": 4.4e-6,
        "cache_read": 0.26e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "glm-5.1": {
        "display_name": "GLM-5.1 (Z.AI)",
        "provider": "zhipu",
        "prompt": 1.4e-6, "completion": 4.4e-6,
        "cache_read": 0.26e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "glm-5": {
        "display_name": "GLM-5 (Z.AI)",
        "provider": "zhipu",
        "prompt": 1.0e-6, "completion": 3.2e-6,
        "cache_read": 0.2e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "glm-5-turbo": {
        "display_name": "GLM-5 Turbo (Z.AI)",
        "provider": "zhipu",
        "prompt": 1.2e-6, "completion": 4.0e-6,
        "cache_read": 0.24e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "glm-4.7": {
        "display_name": "GLM-4.7 (Z.AI)",
        "provider": "zhipu",
        "prompt": 0.6e-6, "completion": 2.2e-6,
        "cache_read": 0.11e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    "glm-4.6": {
        "display_name": "GLM-4.6 (Z.AI)",
        "provider": "zhipu",
        "prompt": 0.6e-6, "completion": 2.2e-6,
        "cache_read": 0.11e-6, "cache_write": 0.0, "reasoning": 0.0,
    },
    # --- Google Gemini (flash-tier fallback for antigravity model names) ---
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

# Only truly-local Ollama models (local/*) and synthetic events are €0.
# GLM cloud models are billed at Z.AI rates above.
FREE_MODEL_PREFIXES = ("<synthetic>", "local/")


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
