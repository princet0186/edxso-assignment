"""Engine creation and schema setup."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine

from outreach import models  # noqa: F401  (registers tables on SQLModel.metadata)
from outreach.config import PROJECT_ROOT, load_secrets

SQLITE_PREFIX = "sqlite:///"
SQLITE_BUSY_TIMEOUT_MS = 5000


def _resolve_sqlite_url(database_url: str) -> str:
    """Relative SQLite paths resolve against the project root, not the current directory,
    so the CLI, API and Streamlit always open the same file wherever they are started."""
    if not database_url.startswith(SQLITE_PREFIX):
        return database_url
    raw_path = database_url.removeprefix(SQLITE_PREFIX)
    if raw_path == ":memory:":
        return database_url
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"{SQLITE_PREFIX}{db_path}"


def _configure_sqlite_connection(dbapi_connection: Any, _record: Any) -> None:
    # WAL lets the Streamlit UI read while a pipeline stage writes; busy_timeout makes a
    # brief writer conflict wait instead of failing immediately.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_db_engine(database_url: str) -> Engine:
    url = _resolve_sqlite_url(database_url)
    if not url.startswith(SQLITE_PREFIX):
        return create_engine(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


@lru_cache
def get_engine() -> Engine:
    return create_db_engine(load_secrets().database_url)


def init_db(engine: Engine) -> None:
    """Creates missing tables. Existing tables are left untouched, so this is safe to re-run."""
    SQLModel.metadata.create_all(engine)
