"""
app/services/conversation_service.py
Session and conversation history management.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import ConversationMessage, ConversationSession

logger = logging.getLogger(__name__)
settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationService:

    def get_or_create_session(self, session_id: str, db: Session) -> ConversationSession:
        session = (
            db.query(ConversationSession)
            .filter(ConversationSession.session_id == session_id)
            .first()
        )
        if not session:
            session = ConversationSession(session_id=session_id, is_active=True)
            db.add(session)
            db.commit()
            logger.debug("Created new session: %s", session_id[:12])
        return session

    def get_history(self, session_id: str, db: Session) -> List[dict]:
        """Return the last N conversation turns for this session."""
        messages = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )
        # Keep last MAX_CONVERSATION_TURNS * 2 messages (each turn = user + assistant)
        limit = settings.MAX_CONVERSATION_TURNS * 2
        messages = messages[-limit:] if len(messages) > limit else messages
        return [{"role": m.role, "content": m.content} for m in messages]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        db: Session,
    ) -> None:
        msg = ConversationMessage(
            session_id=session_id,
            role=role,
            content=content[:4000],   # cap stored content
        )
        db.add(msg)
        # Update session last_active
        session = db.query(ConversationSession).filter(
            ConversationSession.session_id == session_id
        ).first()
        if session:
            session.last_active = _utcnow()
        db.commit()

    def reset_session(self, session_id: str, db: Session) -> None:
        """Delete all messages for this session."""
        db.query(ConversationMessage).filter(
            ConversationMessage.session_id == session_id
        ).delete()
        session = db.query(ConversationSession).filter(
            ConversationSession.session_id == session_id
        ).first()
        if session:
            session.last_active = _utcnow()
        db.commit()
        logger.info("Reset session: %s", session_id[:12])

    def get_context_summary(self, history: List[dict], max_chars: int = 400) -> str:
        """Return a compact string of recent history for retrieval context."""
        if not history:
            return ""
        recent = history[-4:] if len(history) > 4 else history
        return " ".join(
            f"{t['role']}: {t['content'][:100]}" for t in recent
        )[:max_chars]


conversation_service = ConversationService()

