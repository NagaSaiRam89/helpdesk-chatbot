"""
app/api/health.py — Health and readiness endpoint.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import get_settings
from app.database.connection import get_db
from app.schemas.chat import HealthResponse
from app.services.document_service import document_service

router = APIRouter()
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    # Check DB
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="ok",
        database=db_status,
        documents_loaded=document_service.get_document_count(),
        gemini_configured=bool(settings.GEMINI_API_KEY),
    )


@router.get("/documents")
def list_documents():
    return {
        "documents": document_service.get_document_names(),
        "count": document_service.get_document_count(),
    }

