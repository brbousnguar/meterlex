from sqlalchemy import text

import database


def test_schema_contains_expected_tables(engine):
    with engine.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"usage_turns", "model_prices", "settings", "manual_bills", "scan_state"} <= names


def test_sqlite_foreign_connection_is_usable(engine):
    with engine.begin() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
