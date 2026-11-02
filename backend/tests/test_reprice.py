"""Repricing: covers every turn, in pages, and a boot only reprices a new card."""
import asyncio
from datetime import datetime

from sqlmodel import Session

import ingest
import pricing
from models import ModelPrice, UsageTurn


def _turns(session, n):
    for i in range(n):
        session.add(UsageTurn(source="claude-code", session_id="s", turn_key=str(i), model_id="m",
                              ts=datetime(2026, 1, 1), input_tokens=1_000_000, machine="hub"))
    session.commit()


def test_recompute_reprices_every_turn_across_pages(engine, session, monkeypatch):
    monkeypatch.setattr(ingest, "engine", engine)
    _turns(session, 2500)
    session.add(ModelPrice(model_id="m", prompt=2e-6, source="seed"))
    session.commit()

    assert ingest.recompute_costs() == 2500

    with Session(engine) as fresh:
        assert all(t.cost_usd == 2.0 for t in fresh.query(UsageTurn).all())


def _boot(monkeypatch, engine, changed):
    calls = []
    async def pull(_session):
        return {"synced": True, "changed": changed, "count": 1, "error": None}
    monkeypatch.setattr(ingest, "engine", engine)
    monkeypatch.setattr(ingest, "create_db", lambda: None)
    monkeypatch.setattr(pricing, "mirror_pull_prices", pull)
    monkeypatch.setattr(ingest, "recompute_costs", lambda: calls.append(1) or 0)
    asyncio.run(ingest.price_loop())
    return calls


def test_boot_with_an_unchanged_card_does_not_reprice(engine, monkeypatch):
    assert _boot(monkeypatch, engine, changed=False) == []


def test_boot_with_a_new_card_reprices(engine, monkeypatch):
    assert _boot(monkeypatch, engine, changed=True) == [1]
