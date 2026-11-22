import os
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
# setdefault keeps a DB_PATH that is already set: inside the container, the live one (#101).
_db = os.environ.get("DB_PATH")
_tmp = tuple(os.path.realpath(d) + os.sep for d in (tempfile.gettempdir(), "/tmp", "/private/tmp"))
if _db and _db != ":memory:" and not os.path.realpath(_db).startswith(_tmp):
    raise SystemExit(
        f"Refusing to run the tests: DB_PATH={_db} points at a real database, not a throwaway one. "
        "Tests never run against a real database: run them in a local venv, or in a throwaway container "
        "(docker run --rm, no data mount), with DB_PATH unset or under /tmp. See the Cinelex incident of "
        "2026-10-25, when tests run inside the live container dropped every table."
    )
os.environ.setdefault("DB_PATH", ":memory:")

from main import app  # noqa: E402
from database import get_session  # noqa: E402


@pytest.fixture()
def engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    return test_engine


@pytest.fixture()
def session(engine):
    with Session(engine) as db:
        yield db


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
