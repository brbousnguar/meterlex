"""Machine keys: each collector sends usage with its own bearer key.

Keys are shown once when created and stored only as a SHA-256 hash.
"""
import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from models import Machine

LABELS = ("full", "basename", "hash")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "mlx_" + secrets.token_urlsafe(24)


def create(session: Session, name: str, labels: str = "full") -> str:
    """Add (or re-key) a machine; returns its new key."""
    if labels not in LABELS:
        raise ValueError(f"labels must be one of {', '.join(LABELS)}")
    key = new_key()
    m = session.get(Machine, name) or Machine(name=name, key_hash="")
    m.key_hash, m.labels, m.revoked = hash_key(key), labels, False
    m.created_at = m.created_at or datetime.utcnow()
    session.add(m)
    session.commit()
    return key


def revoke(session: Session, name: str) -> bool:
    m = session.get(Machine, name)
    if not m:
        return False
    m.revoked = True
    session.add(m)
    session.commit()
    return True


def authenticate(session: Session, authorization: Optional[str]) -> Optional[Machine]:
    """The machine a `Bearer <key>` header belongs to, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    digest = hash_key(authorization[7:].strip())
    for m in session.exec(select(Machine).where(Machine.revoked == False)).all():  # noqa: E712
        if hmac.compare_digest(m.key_hash, digest):
            return m
    return None
