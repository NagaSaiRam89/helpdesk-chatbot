"""
app/schemas/chat.py — Pydantic request/response models.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
import re

from app.config import get_settings

settings = get_settings()


# ── Request models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)
    request_id: str = Field(..., min_length=8, max_length=128)
    message: str = Field(..., min_length=1, max_length=settings.MAX_MESSAGE_LENGTH)

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message cannot be empty or whitespace only.")
        return stripped

    @field_validator("session_id", "request_id")
    @classmethod
    def alphanumeric_id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9\-_]+$", v):
            raise ValueError("ID must contain only alphanumeric characters, hyphens, or underscores.")
        return v


class SessionResetRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)


# ── Response models ───────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    answer: str
    sources: List[str]
    cached: bool
    session_id: str
    is_supported: bool = True


class StatsResponse(BaseModel):
    total_questions: int
    gemini_api_calls: int
    cache_hits: int


class HealthResponse(BaseModel):
    status: str
    database: str
    documents_loaded: int
    gemini_configured: bool
    version: str = "1.0.0"


class DocumentsResponse(BaseModel):
    documents: List[str]
    count: int


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None

