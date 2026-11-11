"""
tests/test_rate_limit.py — Per-session / per-IP rate limiting tests.
"""
import uuid
import pytest
from unittest.mock import patch, AsyncMock

from app.config import get_settings
from app.services.rate_limit_service import RateLimitService


settings = get_settings()


def gemini_mock():
    from app.services.gemini_service import GeminiResponse
    return AsyncMock(return_value=GeminiResponse(
        answer="The exam is on 20 November 2026.",
        sources=["04_exam_rules_and_faq.txt"],
        is_supported=True,
    ))


class TestRateLimitServiceUnit:
    def test_allows_under_limit(self):
        svc = RateLimitService()
        assert all(svc.allow("k", 3, 60) for _ in range(3))

    def test_blocks_over_limit(self):
        svc = RateLimitService()
        for _ in range(3):
            assert svc.allow("k", 3, 60)
        assert svc.allow("k", 3, 60) is False

    def test_independent_keys(self):
        svc = RateLimitService()
        for _ in range(3):
            svc.allow("a", 3, 60)
        assert svc.allow("b", 3, 60) is True

    def test_window_slides(self):
        svc = RateLimitService()
        # Simulate expired entries by injecting old timestamps
        import time
        q = svc._windows.setdefault("k", __import__("collections").deque())
        q.append(time.monotonic() - 120)
        q.append(time.monotonic() - 120)
        q.append(time.monotonic() - 120)
        # All three entries are older than a 60 s window → still allowed
        assert svc.allow("k", 3, 60) is True

    def test_zero_limit_disables(self):
        svc = RateLimitService()
        assert svc.allow("k", 0, 60) is True


class TestRateLimitEndpoint:
    def test_session_rate_limit_returns_429(self, fresh_client, monkeypatch):
        from app.services.rate_limit_service import rate_limit_service
        rate_limit_service.reset()
        monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 3)

        sid = "s-rl-" + uuid.uuid4().hex[:8]
        with patch("app.services.gemini_service.GeminiService.generate", gemini_mock()):
            for _ in range(3):
                r = fresh_client.post("/api/chat", json={
                    "session_id": sid,
                    "request_id": "r-rl-" + uuid.uuid4().hex[:8],
                    "message": "When is the final exam scheduled?",
                })
                assert r.status_code == 200

            r4 = fresh_client.post("/api/chat", json={
                "session_id": sid,
                "request_id": "r-rl-" + uuid.uuid4().hex[:8],
                "message": "When is the final exam scheduled again?",
            })
        assert r4.status_code == 429
        assert "quickly" in r4.json()["detail"].lower()
        assert "Retry-After" in r4.headers

    def test_other_sessions_not_affected(self, fresh_client, monkeypatch):
        from app.services.rate_limit_service import rate_limit_service
        rate_limit_service.reset()
        monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 2)

        sid_a = "s-rl-a-" + uuid.uuid4().hex[:8]
        sid_b = "s-rl-b-" + uuid.uuid4().hex[:8]

        with patch("app.services.gemini_service.GeminiService.generate", gemini_mock()):
            for _ in range(2):
                fresh_client.post("/api/chat", json={
                    "session_id": sid_a,
                    "request_id": "r-rl-" + uuid.uuid4().hex[:8],
                    "message": "When is the final exam scheduled?",
                })
            blocked = fresh_client.post("/api/chat", json={
                "session_id": sid_a,
                "request_id": "r-rl-" + uuid.uuid4().hex[:8],
                "message": "Another question from a?",
            })
            other = fresh_client.post("/api/chat", json={
                "session_id": sid_b,
                "request_id": "r-rl-" + uuid.uuid4().hex[:8],
                "message": "When is the final exam scheduled?",
            })

        assert blocked.status_code == 429
        assert other.status_code == 200

    def test_ip_rate_limit(self, fresh_client, monkeypatch):
        from app.services.rate_limit_service import rate_limit_service
        rate_limit_service.reset()
        monkeypatch.setattr(settings, "RATE_LIMIT_IP_REQUESTS", 2)

        # Different sessions, same client IP (TestClient) → IP limit applies
        with patch("app.services.gemini_service.GeminiService.generate", gemini_mock()):
            for i in range(2):
                r = fresh_client.post("/api/chat", json={
                    "session_id": f"s-ip-{i}-" + uuid.uuid4().hex[:8],
                    "request_id": "r-ip-" + uuid.uuid4().hex[:8],
                    "message": "When is the final exam scheduled?",
                })
                assert r.status_code == 200

            r3 = fresh_client.post("/api/chat", json={
                "session_id": "s-ip-3-" + uuid.uuid4().hex[:8],
                "request_id": "r-ip-" + uuid.uuid4().hex[:8],
                "message": "When is the final exam scheduled?",
            })
        assert r3.status_code == 429
