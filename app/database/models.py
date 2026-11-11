"""
app/database/models.py — SQLAlchemy ORM models.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, Index,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.database.connection import Base


def _utcnow():
    return datetime.now(timezone.utc)


# ── Analytics counters ────────────────────────────────────────────────────────

class AppCounter(Base):
    """Single-row table for atomic global counters."""
    __tablename__ = "app_counters"

    id = Column(Integer, primary_key=True, default=1)
    total_questions = Column(BigInteger, default=0, nullable=False)
    gemini_api_calls = Column(BigInteger, default=0, nullable=False)
    cache_hits = Column(BigInteger, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ── Response cache ────────────────────────────────────────────────────────────

class CachedResponse(Base):
    """Persistent cache of validated Gemini responses."""
    __tablename__ = "cached_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(128), nullable=False, unique=True, index=True)
    question_normalized = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sources = Column(Text, nullable=False)          # JSON list
    is_supported = Column(Boolean, default=True)
    doc_fingerprint = Column(String(64), nullable=False)
    policy_version = Column(String(16), nullable=False)
    model_version = Column(String(64), nullable=False)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_cached_expires", "expires_at"),
    )


# ── Conversation sessions ─────────────────────────────────────────────────────

class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    last_active = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    is_active = Column(Boolean, default=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    role = Column(String(16), nullable=False)       # "user" or "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_msg_session_created", "session_id", "created_at"),
    )


# ── Idempotency / duplicate request protection ────────────────────────────────

class ProcessedRequest(Base):
    """Tracks accepted request IDs to prevent duplicate question counting."""
    __tablename__ = "processed_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(128), nullable=False, unique=True, index=True)
    session_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

