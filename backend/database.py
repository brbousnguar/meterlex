import os
from sqlmodel import SQLModel, create_engine, Session

DB_PATH = os.getenv("DB_PATH", "/app/data/agentic-spend.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def create_db():
    import models  # noqa: F401 — ensure all tables are registered before create_all
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
