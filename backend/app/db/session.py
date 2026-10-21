"""Engine / session factory bound to Settings.database_url (psycopg v3 driver)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


@lru_cache
def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    return create_engine(url, pool_pre_ping=True, future=True)


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    settings = settings or get_settings()
    return sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """One transaction: commit on success, rollback on error."""
    maker = get_sessionmaker(settings)
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_reachable(settings: Settings | None = None) -> bool:
    try:
        with get_engine((settings or get_settings()).database_url).connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False
