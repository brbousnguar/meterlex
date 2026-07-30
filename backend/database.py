import os
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session

DB_PATH = os.getenv("DB_PATH", "/app/data/meterlex.db")
# timeout: how long a connection waits on SQLite's write lock before raising
# "database is locked" (default is 5s, too short given the ingest loop can
# hold the lock across a multi-file scan commit).
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    # WAL lets readers (API requests) proceed while the ingest loop is
    # mid-write, instead of blocking/erroring on the writer's lock.
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_db():
    import models  # noqa: F401 — ensure all tables are registered before create_all
    SQLModel.metadata.create_all(engine)
    _migrate_columns()


def _migrate_columns():
    # SQLModel's create_all() never alters existing tables, so new columns
    # need an explicit ALTER TABLE guarded by a schema check.
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(scan_state)")}
        if "last_model" not in cols:
            conn.exec_driver_sql("ALTER TABLE scan_state ADD COLUMN last_model TEXT")
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
