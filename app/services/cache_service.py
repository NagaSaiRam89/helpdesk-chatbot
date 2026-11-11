"""
app/services/cache_service.py
Persistent cache with:
- Composite cache key (question + context + doc fingerprint + policy version + model)
- TTL-based expiry
- Cache invalidation on fingerprint change
- Atomic DB writes
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import CachedResponse

logger = logging.getLogger(__name__)
settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_question(question: str) -> str:
    """
    Normalize for cache-key purposes:
    - Lowercase
    - Strip leading/trailing whitespace
    - Collapse repeated whitespace
    - Normalize common punctuation differences
    """
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)
    # Normalize apostrophes and common punctuation variants
    q = q.replace("\u2019", "'").replace("\u2018", "'")
    q = q.replace("\u201c", '"').replace("\u201d", '"')
    return q


def make_cache_key(
    question_normalized: str,
    context_summary: str,
    doc_fingerprint: str,
    policy_version: str,
    model_version: str,
) -> str:
    """Generate a deterministic cache key from all relevant factors."""
    raw = "|".join([
        question_normalized,
        context_summary,
        doc_fingerprint,
        policy_version,
        model_version,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_context_summary(conversation_history: Optional[List[dict]]) -> str:
    """Create a compact representation of recent context for the cache key."""
    if not conversation_history:
        return ""
    # Include the last 2 turns only
    recent = conversation_history[-4:] if len(conversation_history) > 4 else conversation_history
    parts = [f"{t['role']}:{t['content'][:80]}" for t in recent]
    return "|".join(parts)


def effective_context_summary(
    question_normalized: str,
    conversation_history: Optional[List[dict]],
) -> str:
    """
    Context used for cache / single-flight keys.

    Trailing turns that merely repeat the current question are removed first:
    asking the same question again (immediately, with different capitalization
    or spacing) does not change what the question means, so it must hit the
    cache entry created by the earlier ask. Context that interleaves *other*
    turns is kept — genuinely contextual follow-ups must not be conflated
    with context-free asks of the same words.
    """
    h = list(conversation_history or [])
    while len(h) >= 2:
        last_user, last_assistant = h[-2], h[-1]
        if (
            last_user.get("role") == "user"
            and last_assistant.get("role") == "assistant"
            and normalize_question(last_user.get("content", "")) == question_normalized
        ):
            h = h[:-2]
        else:
            break
    return _make_context_summary(h)


class CacheService:

    def get(
        self,
        question: str,
        conversation_history: Optional[List[dict]],
        doc_fingerprint: str,
        db: Session,
    ) -> Optional[CachedResponse]:
        """Return a cached response if valid, else None."""
        key = self._build_key(question, conversation_history, doc_fingerprint)
        now = _utcnow()

        entry = (
            db.query(CachedResponse)
            .filter(
                CachedResponse.cache_key == key,
                CachedResponse.expires_at > now,
            )
            .first()
        )

        if entry:
            # Bump hit count
            entry.hit_count += 1
            db.commit()
            logger.debug("Cache HIT for key=%s", key[:12])
            return entry

        logger.debug("Cache MISS for key=%s", key[:12])
        return None

    def put(
        self,
        question: str,
        conversation_history: Optional[List[dict]],
        doc_fingerprint: str,
        answer: str,
        sources: List[str],
        is_supported: bool,
        db: Session,
    ) -> None:
        """Store a validated response in cache."""
        if not is_supported:
            # Never cache unsupported / fallback responses
            return

        key = self._build_key(question, conversation_history, doc_fingerprint)
        q_norm = normalize_question(question)
        expires = _utcnow() + timedelta(seconds=settings.CACHE_TTL_SECONDS)

        # Upsert
        existing = db.query(CachedResponse).filter(
            CachedResponse.cache_key == key
        ).first()

        if existing:
            existing.answer = answer
            existing.sources = json.dumps(sources)
            existing.expires_at = expires
        else:
            entry = CachedResponse(
                cache_key=key,
                question_normalized=q_norm,
                answer=answer,
                sources=json.dumps(sources),
                is_supported=is_supported,
                doc_fingerprint=doc_fingerprint,
                policy_version=settings.POLICY_CONFIG_VERSION,
                model_version=settings.GEMINI_MODEL,
                hit_count=0,
                expires_at=expires,
            )
            db.add(entry)

        db.commit()
        logger.debug("Cache PUT for key=%s", key[:12])

        # Enforce a bounded cache size (drop oldest entries beyond the cap)
        try:
            self._enforce_size_cap(db)
        except Exception:
            logger.warning("Cache size-cap enforcement failed", exc_info=True)

    def _enforce_size_cap(self, db: Session) -> None:
        limit = settings.MAX_CACHE_ENTRIES
        total = db.query(CachedResponse).count()
        if total <= limit:
            return
        keep_ids = [
            row.id
            for row in db.query(CachedResponse)
            .order_by(CachedResponse.created_at.desc(), CachedResponse.id.desc())
            .limit(limit)
            .all()
        ]
        if keep_ids:
            deleted = (
                db.query(CachedResponse)
                .filter(~CachedResponse.id.in_(keep_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
            logger.info("Cache size cap: removed %d oldest entries", deleted)

    def invalidate_stale(self, current_fingerprint: str, db: Session) -> int:
        """Remove all cache entries belonging to an outdated document corpus.
        (Cache keys already embed the fingerprint so stale entries are never
        served; this purges them from storage at startup.)"""
        deleted = (
            db.query(CachedResponse)
            .filter(CachedResponse.doc_fingerprint != current_fingerprint)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Cache invalidated %d stale entries (fingerprint changed)", deleted)
        return deleted

    def purge_expired(self, db: Session) -> int:
        """Delete entries whose TTL has elapsed (housekeeping at startup)."""
        now = _utcnow()
        deleted = (
            db.query(CachedResponse)
            .filter(CachedResponse.expires_at <= now)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Cache purged %d expired entries", deleted)
        return deleted

    def _build_key(
        self,
        question: str,
        conversation_history: Optional[List[dict]],
        doc_fingerprint: str,
    ) -> str:
        return make_cache_key(
            question_normalized=normalize_question(question),
            context_summary=effective_context_summary(
                normalize_question(question), conversation_history
            ),
            doc_fingerprint=doc_fingerprint,
            policy_version=settings.POLICY_CONFIG_VERSION,
            model_version=settings.GEMINI_MODEL,
        )


cache_service = CacheService()

