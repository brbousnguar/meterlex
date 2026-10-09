from sqlalchemy import text

import database


def test_schema_contains_expected_tables(engine):
    with engine.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"usage_turns", "model_prices", "settings", "manual_bills", "scan_state"} <= names


def test_sqlite_foreign_connection_is_usable(engine):
    with engine.begin() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_an_existing_database_gains_the_branch_column():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    old = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with old.begin() as conn:  # usage_turns as it was before branches
        conn.execute(text("CREATE TABLE usage_turns (id INTEGER PRIMARY KEY, source VARCHAR, session_id VARCHAR, "
                          "turn_key VARCHAR, project VARCHAR, model_id VARCHAR, ts DATETIME, "
                          "machine VARCHAR NOT NULL DEFAULT 'hub', origin VARCHAR)"))
    database.create_db(old)
    with old.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usage_turns)"))}
        conn.execute(text("INSERT INTO usage_turns (id, branch) VALUES (1, 'HEAD'), (2, 'main')"))
    assert "branch" in cols
    database.create_db(old)  # Claude Code's HEAD outside a repository is no branch
    with old.connect() as conn:
        assert dict(conn.execute(text("SELECT id, branch FROM usage_turns")).all()) == {1: None, 2: "main"}


def test_stored_model_spellings_merge_into_one_name(engine):
    from datetime import datetime
    from sqlmodel import Session
    from models import UsageTurn
    with Session(engine) as session:
        for i, (src, model) in enumerate([("copilot", "claude-haiku-4.5"), ("claude-code", "claude-haiku-4-5-20251001"),
                                          ("ollama", "glm-5.2")]):
            session.add(UsageTurn(source=src, session_id=str(i), turn_key="1", model_id=model, ts=datetime(2026, 10, 1)))
        session.commit()
    database.create_db(engine)
    with engine.connect() as conn:
        ids = sorted(r[0] for r in conn.execute(text("SELECT model_id FROM usage_turns")))
    assert ids == ["claude-haiku-4-5", "claude-haiku-4-5", "glm-5.2"]
