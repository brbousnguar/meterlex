"""SQLModel schema for the Meterlex tracker."""
import os
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint

# The machine the hub's own history came from, before collectors reported
# per machine (set HUB_MACHINE in .env to its collector's machine name).
HUB_MACHINE = os.getenv("HUB_MACHINE", "hub")


class UsageTurn(SQLModel, table=True):
    __tablename__ = "usage_turns"
    __table_args__ = (
        UniqueConstraint("source", "session_id", "turn_key", name="uq_turn"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # claude-code | ollama | codex | antigravity | gemini-cli | copilot
    source: str = Field(index=True)
    session_id: str = Field(index=True)
    # claude-code: the reply's message id (older rows: a per-line uuid);
    # codex: ISO ts; gemini-cli: message id; snapshots: "session[:model]"
    turn_key: str
    project: Optional[str] = None       # cwd, or its label (see Machine.labels)
    model_id: str = Field(index=True)
    ts: datetime = Field(index=True)
    machine: str = Field(default=HUB_MACHINE, index=True)
    origin: Optional[str] = None        # interactive | automated | subagent
    branch: Optional[str] = None        # git branch of the working folder (Claude Code), or its label
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0               # for agy: step_count proxy
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    fx_rate: float = 0.92
    price_source: str = "free"          # openrouter | ollama | mirrored | free
    file_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Machine(SQLModel, table=True):
    """A machine whose collector may send usage, with its key (hashed)."""
    __tablename__ = "machines"

    name: str = Field(primary_key=True)
    key_hash: str
    labels: str = "full"                 # full | basename | hash: how its projects are stored
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: Optional[datetime] = None
    last_batch: int = 0
    turns_received: int = 0
    collector_version: Optional[str] = None
    revoked: bool = False


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
    """Offsets of the in-container scanner, which collectors replaced. Kept so
    existing databases keep their schema."""
    __tablename__ = "scan_state"

    file_path: str = Field(primary_key=True)
    last_offset: int = 0
    last_scanned_at: datetime = Field(default_factory=datetime.utcnow)
    file_size: int = 0
    last_model: Optional[str] = None


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = ""
