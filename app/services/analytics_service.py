"""
app/services/analytics_service.py
Database-backed atomic counters for live usage statistics.
"""
from __future__ import annotations

import logging
from typing import Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.models import AppCounter

logger = logging.getLogger(__name__)


class AnalyticsService:

    def ensure_counter_row(self, db: Session) -> None:
        """Create the single counter row if it doesn't exist."""
        exists = db.query(AppCounter).filter(AppCounter.id == 1).first()
        if not exists:
            db.add(AppCounter(id=1, total_questions=0, gemini_api_calls=0, cache_hits=0))
            db.commit()

    def get_stats(self, db: Session) -> dict:
        self.ensure_counter_row(db)
        row = db.query(AppCounter).filter(AppCounter.id == 1).first()
        return {
            "total_questions": row.total_questions if row else 0,
            "gemini_api_calls": row.gemini_api_calls if row else 0,
            "cache_hits": row.cache_hits if row else 0,
        }

    def increment_question(self, db: Session) -> None:
        """Atomic increment of total_questions counter."""
        self.ensure_counter_row(db)
        db.query(AppCounter).filter(AppCounter.id == 1).update(
            {AppCounter.total_questions: AppCounter.total_questions + 1}
        )
        db.commit()

    def increment_gemini_call(self, db: Session) -> None:
        """Atomic increment of gemini_api_calls counter."""
        self.ensure_counter_row(db)
        db.query(AppCounter).filter(AppCounter.id == 1).update(
            {AppCounter.gemini_api_calls: AppCounter.gemini_api_calls + 1}
        )
        db.commit()

    def increment_cache_hit(self, db: Session) -> None:
        """Atomic increment of cache_hits counter."""
        self.ensure_counter_row(db)
        db.query(AppCounter).filter(AppCounter.id == 1).update(
            {AppCounter.cache_hits: AppCounter.cache_hits + 1}
        )
        db.commit()

    def increment_question_and_cache(self, db: Session) -> None:
        """Cache hit: increment both total_questions and cache_hits atomically."""
        self.ensure_counter_row(db)
        db.query(AppCounter).filter(AppCounter.id == 1).update({
            AppCounter.total_questions: AppCounter.total_questions + 1,
            AppCounter.cache_hits: AppCounter.cache_hits + 1,
        })
        db.commit()

    def increment_question_and_gemini(self, db: Session) -> None:
        """Successful Gemini response: increment total_questions and gemini_api_calls."""
        self.ensure_counter_row(db)
        db.query(AppCounter).filter(AppCounter.id == 1).update({
            AppCounter.total_questions: AppCounter.total_questions + 1,
            AppCounter.gemini_api_calls: AppCounter.gemini_api_calls + 1,
        })
        db.commit()


analytics_service = AnalyticsService()

