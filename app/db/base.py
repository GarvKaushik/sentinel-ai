"""SQLAlchemy engine, sessions, and table setup.

Persistence is opt-in via DATABASE_URL: unset -> engine_available() is False and
every repository call is a no-op (local dev, eval, and tests still run); set ->
a lazily-created engine backs the investigations table.

The URL is read lazily (not at import) so tests can point it at a throwaway
SQLite file first.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base every ORM model inherits from."""


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _database_url() -> Optional[str]:
    return os.environ.get("DATABASE_URL")


def engine_available() -> bool:
    """True when a DB is configured. Guards every repo call so the API degrades
    to 'no history' instead of crashing when Postgres is off."""
    return bool(_database_url())


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _database_url()
        if not url:
            raise RuntimeError("DATABASE_URL is not set; persistence is disabled")
        # pool_pre_ping recycles connections dropped by Postgres between requests.
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        # expire_on_commit=False lets us read a row's attributes after commit,
        # e.g. return the freshly-assigned primary key from session_scope().
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error, always close."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(retries: int = 5, delay: float = 2.0) -> bool:
    """Create the tables if a DB is set up and reachable. Retries a few times so
    a DB that's still starting up (the docker-compose race) doesn't permanently
    disable persistence. Returns True when ready, False otherwise — the app runs
    either way."""
    if not engine_available():
        return False
    from app.db import models  # noqa: F401 — register tables on Base.metadata

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(get_engine())
            return True
        except Exception as exc:  # DB not up yet, bad URL, etc. — don't crash the app
            last_exc = exc
            if attempt < retries:
                log.warning(
                    "init_db attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt, retries, exc, delay,
                )
                time.sleep(delay)
    log.warning("init_db gave up after %d attempts (persistence unavailable): %s", retries, last_exc)
    return False


def reset_engine_for_tests() -> None:
    """Drop the cached engine/session factory so a test can re-point DATABASE_URL."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
