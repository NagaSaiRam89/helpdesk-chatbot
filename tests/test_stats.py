"""
tests/test_stats.py — Analytics counter tests.
"""
import uuid
import pytest
from unittest.mock import patch, AsyncMock


def make_gemini_mock(answer="OK", sources=None):
    from app.services.gemini_service import GeminiResponse
    return AsyncMock(return_value=GeminiResponse(
        answer=answer,
        sources=sources or ["03_grading_policy_v2.txt"],
        is_supported=True,
    ))


class TestAnalyticsService:
    def test_ensure_counter_row(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        stats = analytics_service.get_stats(db_session)
        assert isinstance(stats["total_questions"], int)
        assert isinstance(stats["gemini_api_calls"], int)
        assert isinstance(stats["cache_hits"], int)

    def test_increment_question(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        before = analytics_service.get_stats(db_session)["total_questions"]
        analytics_service.increment_question(db_session)
        after = analytics_service.get_stats(db_session)["total_questions"]
        assert after == before + 1

    def test_increment_gemini_call(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        before = analytics_service.get_stats(db_session)["gemini_api_calls"]
        analytics_service.increment_gemini_call(db_session)
        after = analytics_service.get_stats(db_session)["gemini_api_calls"]
        assert after == before + 1

    def test_increment_cache_hit(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        before = analytics_service.get_stats(db_session)["cache_hits"]
        analytics_service.increment_cache_hit(db_session)
        after = analytics_service.get_stats(db_session)["cache_hits"]
        assert after == before + 1

    def test_cache_hit_increments_total_and_cache(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        before = analytics_service.get_stats(db_session)
        analytics_service.increment_question_and_cache(db_session)
        after = analytics_service.get_stats(db_session)
        assert after["total_questions"] == before["total_questions"] + 1
        assert after["cache_hits"] == before["cache_hits"] + 1
        assert after["gemini_api_calls"] == before["gemini_api_calls"]  # unchanged

    def test_gemini_success_increments_total_and_gemini(self, db_session):
        from app.services.analytics_service import analytics_service
        analytics_service.ensure_counter_row(db_session)
        before = analytics_service.get_stats(db_session)
        analytics_service.increment_question_and_gemini(db_session)
        after = analytics_service.get_stats(db_session)
        assert after["total_questions"] == before["total_questions"] + 1
        assert after["gemini_api_calls"] == before["gemini_api_calls"] + 1
        assert after["cache_hits"] == before["cache_hits"]  # unchanged


class TestStatsEndpoint:
    def test_stats_endpoint_ok(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_questions" in data
        assert "gemini_api_calls" in data
        assert "cache_hits" in data

    def test_stats_values_are_non_negative(self, client):
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["total_questions"] >= 0
        assert data["gemini_api_calls"] >= 0
        assert data["cache_hits"] >= 0

    def test_stats_increases_after_question(self, client):
        stats_before = client.get("/api/stats").json()

        question = "stats-change test: " + uuid.uuid4().hex
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock("Test answer", ["01_course_handbook.txt"])):
            client.post("/api/chat", json={
                "session_id": "s-statsinc-" + uuid.uuid4().hex[:8],
                "request_id": "r-statsinc-" + uuid.uuid4().hex[:8],
                "message": question,
            })

        stats_after = client.get("/api/stats").json()
        assert stats_after["total_questions"] > stats_before["total_questions"]

