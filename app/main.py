"""
app/main.py — FastAPI application entry point.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database.connection import init_db, SessionLocal
from app.services.cache_service import cache_service
from app.services.document_service import document_service
from app.services.retrieval_service import build_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting DPM-101 Helpdesk …")

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Load course documents
    docs_dir = settings.COURSE_DOCS_DIR
    if not os.path.isabs(docs_dir):
        # Resolve relative to project root (where main.py is invoked from)
        docs_dir = str(Path.cwd() / docs_dir)
    document_service.load(docs_dir)

    # Build BM25 index
    build_index()

    # Cache housekeeping: purge expired entries and any entries cached
    # against an outdated document corpus (fingerprint changed).
    doc_fingerprint = document_service.get_corpus_fingerprint()
    try:
        with SessionLocal() as db:
            cache_service.purge_expired(db)
            cache_service.invalidate_stale(doc_fingerprint, db)
    except Exception:
        logger.warning("Cache housekeeping failed (non-fatal)", exc_info=True)

    logger.info("DPM-101 Helpdesk ready ✓")
    yield

    logger.info("Shutting down DPM-101 Helpdesk …")


app = FastAPI(
    title="DPM-101 Course Helpdesk",
    description="AI-powered chatbot for IIMBx Digital Product Management (DPM-101)",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# The app serves its own frontend, so same-origin requests need no CORS at all.
# A wildcard policy is only a development convenience for foreign origins, and
# credentialed requests are never used — so allow_credentials stays False for "*"
# (browsers reject the credentials+wildcard combination outright).
if settings.CORS_ORIGINS.strip() == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
else:
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "An unexpected error occurred. Please try again."},
    )


# ── API routers ───────────────────────────────────────────────────────────────
from app.api import chat, stats, health  # noqa: E402

app.include_router(chat.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(health.router, prefix="/api")


# ── Static files & frontend ───────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"message": "DPM-101 Helpdesk API", "docs": "/docs"})

