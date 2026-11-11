"""
app/config.py — Centralised configuration via environment variables.
"""
import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv

# Load a local .env file if present (real environment variables win).
load_dotenv()


class Settings:
    # ── Gemini ────────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./dpm101_helpdesk.db"
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_ENV: str = os.getenv("APP_ENV", "development")
    PORT: int = int(os.getenv("PORT", "8000"))
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    # ── Course documents ──────────────────────────────────────────────────────
    COURSE_DOCS_DIR: str = os.getenv("COURSE_DOCS_DIR", "course_docs")

    # ── Cache ─────────────────────────────────────────────────────────────────
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "86400"))   # 24 h
    MAX_CACHE_ENTRIES: int = int(os.getenv("MAX_CACHE_ENTRIES", "5000"))

    # ── Concurrency / rate limiting ───────────────────────────────────────────
    MAX_CONCURRENT_GEMINI: int = int(os.getenv("MAX_CONCURRENT_GEMINI", "5"))
    GEMINI_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))
    GEMINI_MAX_RETRIES: int = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
    GEMINI_RETRY_BASE_DELAY: float = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))

    # ── Conversation ──────────────────────────────────────────────────────────
    MAX_CONVERSATION_TURNS: int = int(os.getenv("MAX_CONVERSATION_TURNS", "10"))
    MAX_MESSAGE_LENGTH: int = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))
    SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "7200"))   # 2 h

    # ── Retrieval ─────────────────────────────────────────────────────────────
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
    RETRIEVAL_MIN_SCORE: float = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.05"))
    # Fraction of meaningful question terms that must exist anywhere in the
    # corpus before a question is considered in-scope (out-of-scope guard).
    RETRIEVAL_MIN_TERM_COVERAGE: float = float(os.getenv("RETRIEVAL_MIN_TERM_COVERAGE", "0.3"))
    # Minimum best BM25 score the question alone must achieve before the
    # retrieval falls back to a context-expanded query (anaphoric follow-ups
    # like "What happens if I miss it?" have no lexical anchor of their own).
    RETRIEVAL_ANCHOR_MIN_SCORE: float = float(os.getenv("RETRIEVAL_ANCHOR_MIN_SCORE", "1.0"))

    # ── Policy ────────────────────────────────────────────────────────────────
    POLICY_CONFIG_VERSION: str = "2.0"   # bump when precedence rules change

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))          # per session_id
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_IP_REQUESTS: int = int(os.getenv("RATE_LIMIT_IP_REQUESTS", "300"))  # per client IP (generous for shared NATs)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

