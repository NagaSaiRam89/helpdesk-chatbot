"""
app/api/chat.py — Chat endpoint: orchestrates the full pipeline.

Pipeline per request:
0. Rate-limit check (per session + per IP)
1. Validate request (Pydantic)
2. Check idempotency (duplicate request_id → return 200 without re-counting)
3. Check cache → return immediately if hit
4. Retrieve relevant document chunks (BM25 + out-of-scope gate)
5. Out-of-scope → return without Gemini if no evidence
6. Single-flight deduplication for concurrent identical requests
7. Call Gemini with evidence context + conversation history (each attempt counted)
8. Validate response
9. Store in cache
10. Update analytics counters
11. Store conversation turn
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.connection import get_db
from app.database.models import CachedResponse, ProcessedRequest
from app.schemas.chat import (
    ChatRequest, ChatResponse, SessionResetRequest,
)
from app.services.analytics_service import analytics_service
from app.services.cache_service import cache_service, effective_context_summary, make_cache_key, normalize_question
from app.services.conversation_service import conversation_service
from app.services.document_service import document_service
from app.services.gemini_service import (
    FRIENDLY_ERRORS, GeminiServiceError, gemini_service,
)
from app.services.inflight_service import inflight_service
from app.services.rate_limit_service import rate_limit_service
from app.services.retrieval_service import retrieval_service

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _idempotent_response(session_id: str, db: Session) -> ChatResponse:
    """Safe response for a duplicate request_id — no counters incremented."""
    history = conversation_service.get_history(session_id, db)
    last_answer = "Your question has already been processed."
    for turn in reversed(history):
        if turn["role"] == "assistant":
            last_answer = turn["content"]
            break
    return ChatResponse(
        answer=last_answer,
        sources=[],
        cached=True,
        session_id=session_id,
        is_supported=True,
    )


def _make_attempt_counter(db: Session):
    """Callback invoked once per outbound Gemini attempt (including retries)."""
    def count():
        try:
            analytics_service.increment_gemini_call(db)
        except Exception:
            logger.warning("Failed to increment gemini_api_calls", exc_info=True)
    return count


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session_id = payload.session_id
    request_id = payload.request_id
    question = payload.message.strip()

    # ── 0. Rate limiting (per session and per IP) ────────────────────────────
    client_ip = _get_client_ip(request)
    allowed = (
        rate_limit_service.allow(
            f"session:{session_id}",
            settings.RATE_LIMIT_REQUESTS,
            settings.RATE_LIMIT_WINDOW_SECONDS,
        )
        and rate_limit_service.allow(
            f"ip:{client_ip}",
            settings.RATE_LIMIT_IP_REQUESTS,
            settings.RATE_LIMIT_WINDOW_SECONDS,
        )
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "You are sending messages very quickly. "
                "Please wait a moment and try again."
            ),
            headers={
                "Retry-After": str(
                    rate_limit_service.retry_after_seconds(
                        settings.RATE_LIMIT_WINDOW_SECONDS
                    )
                )
            },
        )

    # ── 1. Idempotency check ───────────────────────────────────────────────────
    existing_req = (
        db.query(ProcessedRequest)
        .filter(ProcessedRequest.request_id == request_id)
        .first()
    )
    if existing_req:
        # Duplicate submission — return a safe response without incrementing counters
        logger.info("Duplicate request_id=%s, returning 200 without re-counting", request_id[:16])
        return _idempotent_response(session_id, db)

    # Mark request as being processed (insert before doing any work).
    # The unique constraint also guards the race where two concurrent
    # requests carrying the same request_id both pass the check above.
    try:
        db.add(ProcessedRequest(request_id=request_id, session_id=session_id))
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("Concurrent duplicate request_id=%s", request_id[:16])
        return _idempotent_response(session_id, db)

    # ── 2. Ensure session exists ───────────────────────────────────────────────
    conversation_service.get_or_create_session(session_id, db)
    history = conversation_service.get_history(session_id, db)
    context_summary = conversation_service.get_context_summary(history)

    # ── 3. Cache lookup ────────────────────────────────────────────────────────
    doc_fingerprint = document_service.get_corpus_fingerprint()
    cached = cache_service.get(question, history, doc_fingerprint, db)
    if cached:
        sources = json.loads(cached.sources)
        analytics_service.increment_question_and_cache(db)
        conversation_service.add_message(db=db, session_id=session_id, role="user", content=question)
        conversation_service.add_message(db=db, session_id=session_id, role="assistant", content=cached.answer)
        logger.info("Cache hit for session=%s", session_id[:12])
        return ChatResponse(
            answer=cached.answer,
            sources=sources,
            cached=True,
            session_id=session_id,
            is_supported=cached.is_supported,
        )

    # ── 4. Document retrieval ──────────────────────────────────────────────────
    retrieval = retrieval_service.retrieve(
        question=question,
        conversation_context=context_summary,
    )

    # ── 5. Out-of-scope check ──────────────────────────────────────────────────
    if retrieval.out_of_scope:
        oos_answer = (
            "This question is not covered in the official DPM-101 course documents. "
            "If you have a question about course content not listed here, "
            "please contact the course support team directly."
        )
        analytics_service.increment_question(db)
        conversation_service.add_message(db=db, session_id=session_id, role="user", content=question)
        conversation_service.add_message(db=db, session_id=session_id, role="assistant", content=oos_answer)
        return ChatResponse(
            answer=oos_answer,
            sources=[],
            cached=False,
            session_id=session_id,
            is_supported=False,
        )

    # ── 6. Single-flight deduplication ────────────────────────────────────────
    inflight_key = make_cache_key(
        normalize_question(question),
        effective_context_summary(normalize_question(question), history),
        doc_fingerprint,
        settings.POLICY_CONFIG_VERSION,
        settings.GEMINI_MODEL,
    )

    is_leader, waiting_future = await inflight_service.get_or_start(inflight_key)

    if not is_leader:
        # Follower: wait for leader's result, count as a question
        try:
            gemini_resp = await waiting_future
            analytics_service.increment_question(db)
            # Cache the result for followers too (leader should have stored it)
            answer = gemini_resp.answer
            sources = gemini_resp.sources
            conversation_service.add_message(db=db, session_id=session_id, role="user", content=question)
            conversation_service.add_message(db=db, session_id=session_id, role="assistant", content=answer)
            return ChatResponse(
                answer=answer,
                sources=sources,
                cached=False,
                session_id=session_id,
                is_supported=gemini_resp.is_supported,
            )
        except GeminiServiceError as e:
            analytics_service.increment_question(db)
            raise HTTPException(status_code=503, detail=e.friendly_message)

    # ── 7. Gemini call (leader) ────────────────────────────────────────────────
    try:
        gemini_resp = await gemini_service.generate(
            question=question,
            evidence_context=retrieval.context_text,
            conversation_history=history,
            source_filenames=retrieval.sources,
            # Counts every actual outbound attempt, including retries
            on_attempt=_make_attempt_counter(db),
        )

        # ── 8. Store in cache ──────────────────────────────────────────────────
        if gemini_resp.is_supported:
            cache_service.put(
                question=question,
                conversation_history=history,
                doc_fingerprint=doc_fingerprint,
                answer=gemini_resp.answer,
                sources=gemini_resp.sources or retrieval.sources,
                is_supported=gemini_resp.is_supported,
                db=db,
            )

        # ── 9. Update analytics ────────────────────────────────────────────────
        analytics_service.increment_question(db)

        # ── 10. Store conversation turn ────────────────────────────────────────
        conversation_service.add_message(db=db, session_id=session_id, role="user", content=question)
        conversation_service.add_message(db=db, session_id=session_id, role="assistant", content=gemini_resp.answer)

        # Broadcast to waiting followers
        await inflight_service.resolve(inflight_key, gemini_resp)

        final_sources = gemini_resp.sources or retrieval.sources
        return ChatResponse(
            answer=gemini_resp.answer,
            sources=final_sources,
            cached=False,
            session_id=session_id,
            is_supported=gemini_resp.is_supported,
        )

    except GeminiServiceError as e:
        await inflight_service.reject(inflight_key, e)
        # Count question even on failure (but gemini_api_calls already counted)
        analytics_service.increment_question(db)
        logger.warning("Gemini error: %s", e.friendly_message)
        raise HTTPException(status_code=503, detail=e.friendly_message)

    except Exception as exc:
        await inflight_service.reject(inflight_key, exc)
        analytics_service.increment_question(db)
        logger.error("Unexpected error in chat: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=FRIENDLY_ERRORS["unavailable"])


@router.post("/session/reset")
async def reset_session(
    payload: SessionResetRequest,
    db: Session = Depends(get_db),
):
    conversation_service.reset_session(payload.session_id, db)
    return {"status": "ok", "session_id": payload.session_id}

