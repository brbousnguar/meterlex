import os
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session

DB_PATH = os.getenv("DB_PATH", "/app/data/meterlex.db")
# timeout: how long a connection waits on SQLite's write lock before raising
# "database is locked" (default is 5s, too short when a large ingest batch
# holds the lock across its commit).
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    # WAL lets readers (API requests) proceed while an ingest batch is
    # mid-write, instead of blocking/erroring on the writer's lock.
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_db(target=None):
    import models  # noqa: F401 — ensure all tables are registered before create_all
    target = target or engine
    SQLModel.metadata.create_all(target)
    _migrate_columns(target)


def _migrate_columns(target):
    # SQLModel's create_all() never alters existing tables, so new columns
    # need an explicit ALTER TABLE guarded by a schema check.
    from models import HUB_MACHINE

    with target.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(scan_state)")}
        if cols and "last_model" not in cols:
            conn.exec_driver_sql("ALTER TABLE scan_state ADD COLUMN last_model TEXT")
        turn_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(usage_turns)")}
        if "machine" not in turn_cols:
            # every row so far came from the hub's own scanner (a column
            # default can't be a bound parameter, hence the literal)
            name = HUB_MACHINE.replace("'", "")
            conn.exec_driver_sql(f"ALTER TABLE usage_turns ADD COLUMN machine VARCHAR NOT NULL DEFAULT '{name}'")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_usage_turns_machine ON usage_turns (machine)")
        if "origin" not in turn_cols:
            conn.exec_driver_sql("ALTER TABLE usage_turns ADD COLUMN origin VARCHAR")
        if "branch" not in turn_cols:
            conn.exec_driver_sql("ALTER TABLE usage_turns ADD COLUMN branch VARCHAR")
        # Claude Code records HEAD outside a repository; collectors 0.2.0 sent it
        conn.exec_driver_sql("UPDATE usage_turns SET branch = NULL WHERE branch = 'HEAD'")
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
