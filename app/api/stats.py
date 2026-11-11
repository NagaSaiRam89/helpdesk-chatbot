"""
app/api/stats.py — Live analytics endpoint.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.chat import StatsResponse
from app.services.analytics_service import analytics_service

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    stats = analytics_service.get_stats(db)
    return StatsResponse(**stats)

