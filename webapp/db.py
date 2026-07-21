"""SQLModel engine + session management (design.md §4.1).

SQLite, single file at `webapp/data/platform.db` — one of the only two locations the platform is
allowed to write (CLAUDE.md §15). The engine is created lazily and can be swapped by tests
(`set_engine`) so the API test-suite runs against a throwaway temp DB without touching the real one.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "platform.db"

_engine = None


def _default_url() -> str:
    # env override lets tests / alternate deployments point elsewhere without code changes
    override = os.environ.get("TIMETABLE_DB_PATH")
    if override:
        return f"sqlite:///{override}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)  # allowed write location (CLAUDE.md §15)
    return f"sqlite:///{DEFAULT_DB_PATH}"


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            _default_url(),
            echo=False,
            # FastAPI runs the sync solver in a threadpool -> connections cross threads
            connect_args={"check_same_thread": False},
        )
    return _engine


def set_engine(engine) -> None:
    """Override the process engine (used by tests to point at a temp SQLite file)."""
    global _engine
    _engine = engine


def init_db() -> None:
    """Create all tables. Import models first so they are registered on SQLModel.metadata."""
    import webapp.models_db  # noqa: F401  (registers table classes)
    SQLModel.metadata.create_all(get_engine())


def get_session():
    """FastAPI dependency yielding a session bound to the current engine."""
    with Session(get_engine()) as session:
        yield session
