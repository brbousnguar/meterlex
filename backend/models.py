"""SQLModel schema for the agentic-spend tracker."""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class UsageTurn(SQLModel, table=True):
    __tablename__ = "usage_turns"
    __table_args__ = (
        UniqueConstraint("source", "session_id", "turn_key", name="uq_turn"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)     # claude-code | codex | antigravity
    session_id: str = Field(index=True)
    turn_key: str                        # uuid (cc), ISO ts (codex), "session" (agy)
    project: Optional[str] = None       # cwd
    model_id: str = Field(index=True)
    ts: datetime = Field(index=True)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0               # for agy: step_count proxy
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    fx_rate: float = 0.92
    price_source: str = "free"          # manual | seed | free
    file_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ModelPrice(SQLModel, table=True):
    __tablename__ = "model_prices"

    model_id: str = Field(primary_key=True)
    display_name: Optional[str] = None
    provider: Optional[str] = None
    prompt: float = 0.0        # USD per single token
    completion: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    reasoning: float = 0.0
    source: str = "manual"
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ManualBill(SQLModel, table=True):
    """Per-month actual subscription cost for variable-rate tools (e.g. GitHub Copilot)."""
    __tablename__ = "manual_bills"

    source: str = Field(primary_key=True)    # e.g. "copilot"
    year_month: str = Field(primary_key=True) # e.g. "2026-01"
    amount_eur: float = 0.0


class ScanState(SQLModel, table=True):
    __tablename__ = "scan_state"

    file_path: str = Field(primary_key=True)
    last_offset: int = 0
    last_scanned_at: datetime = Field(default_factory=datetime.utcnow)
    file_size: int = 0
    last_model: Optional[str] = None    # codex: model in effect at last_offset, for incremental resume


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = ""
