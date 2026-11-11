"""
app/database/connection.py — Database engine and session management.
Supports PostgreSQL (via DATABASE_URL) and SQLite fallback.
"""
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _make_engine():
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Enable WAL mode for better concurrent reads on SQLite
        @event.listens_for(engine, "connect")
        def set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Using SQLite database: %s", url)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
        logger.info("Using PostgreSQL database")
    return engine


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called on application startup."""
    from app.database import models  # noqa: F401 — registers models
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")

