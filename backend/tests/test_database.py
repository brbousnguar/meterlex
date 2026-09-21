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
    with old.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usage_turns)"))}
    assert "branch" in cols
