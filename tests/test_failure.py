"""
tests/test_failure.py — Gemini failure mode tests.
App must NOT crash; must return friendly error messages.
"""
import uuid
import pytest
from unittest.mock import patch, AsyncMock

from app.config import get_settings

settings = get_settings()


def sid():
    return "s-fail-" + uuid.uuid4().hex[:8]

def rid():
    return "r-fail-" + uuid.uuid4().hex[:8]


class TestGeminiFailures:

    def _chat(self, client, msg):
        return client.post("/api/chat", json={
            "session_id": sid(),
            "request_id": rid(),
            "message": msg,
        })

    def test_gemini_rate_limit_429(self, client):
        from app.services.gemini_service import GeminiServiceError, FRIENDLY_ERRORS

        async def raise_rate_limit(*args, **kwargs):
            raise GeminiServiceError("rate_limit", FRIENDLY_ERRORS["rate_limit"], should_retry=False)

        with patch("app.services.gemini_service.GeminiService.generate", side_effect=raise_rate_limit):
            resp = self._chat(client, "What is the exam date? rate-limit-test")

        assert resp.status_code in (200, 503)
        if resp.status_code == 503:
            assert "many requests" in resp.json().get("detail", "").lower() or \
                   "wait" in resp.json().get("detail", "").lower()

    def test_gemini_service_unavailable_503(self, client):
        from app.services.gemini_service import GeminiServiceError, FRIENDLY_ERRORS

        async def raise_unavailable(*args, **kwargs):
            raise GeminiServiceError("unavailable", FRIENDLY_ERRORS["unavailable"], should_retry=False)

        with patch("app.services.gemini_service.GeminiService.generate", side_effect=raise_unavailable):
            resp = self._chat(client, "What is the passing score? unavail-test")

        assert resp.status_code in (200, 503)

    def test_gemini_timeout(self, client):
        import asyncio
        from app.services.gemini_service import GeminiServiceError, FRIENDLY_ERRORS

        async def raise_timeout(*args, **kwargs):
            raise GeminiServiceError("timeout", FRIENDLY_ERRORS["timeout"], should_retry=False)

        with patch("app.services.gemini_service.GeminiService.generate", side_effect=raise_timeout):
            resp = self._chat(client, "When is the capstone due? timeout-test")

        assert resp.status_code in (200, 503)

    def test_malformed_gemini_response(self, client):
        """If Gemini returns non-JSON, the app must handle it gracefully."""
        from app.services.gemini_service import GeminiResponse

        async def return_unsupported(*args, **kwargs):
            return GeminiResponse(
                answer="Our course assistant is temporarily unavailable.",
                sources=[],
                is_supported=False,
            )

        with patch("app.services.gemini_service.GeminiService.generate", side_effect=return_unsupported):
            resp = self._chat(client, "What score to pass? malformed-test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]

    def test_no_relevant_document(self, client):
        """
        Completely unrelated query — retrieval returns nothing.
        App must respond without calling Gemini.
        """
        resp = self._chat(client, "Who is the president of the United States? cricket score?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        assert data["is_supported"] is False or "not covered" in data["answer"].lower() or \
               "not mentioned" in data["answer"].lower() or "course documents" in data["answer"].lower()

    def test_app_does_not_crash_on_any_failure(self, client):
        """The API must always return a JSON response, never a 500 stack trace."""
        from app.services.gemini_service import GeminiServiceError

        async def raise_generic(*args, **kwargs):
            raise Exception("Unexpected internal error")

        with patch("app.services.gemini_service.GeminiService.generate", side_effect=raise_generic):
            resp = self._chat(client, "Test crash prevention")

        assert resp.status_code in (200, 500, 503)
        # Must be JSON
        data = resp.json()
        # Must NOT expose internal details
        content = str(data)
        assert "traceback" not in content.lower()
        assert "exception" not in content.lower()


class TestGeminiRetryBehavior:
    """Retries count as additional actual Gemini API calls (per-attempt counter)."""

    def _service(self):
        from app.services.gemini_service import GeminiService
        return GeminiService()

    def test_retry_counts_each_attempt(self, monkeypatch):
        import asyncio
        from app.services.gemini_service import GeminiServiceError

        svc = self._service()
        attempts = []
        on_attempt_calls = []

        async def failing_call(*args, **kwargs):
            attempts.append(1)
            raise GeminiServiceError(
                "rate_limit", "rate limited", should_retry=True
            )

        monkeypatch.setattr(svc, "_call_gemini", failing_call)
        monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 3)
        monkeypatch.setattr(settings, "GEMINI_RETRY_BASE_DELAY", 0.01)

        with pytest.raises(GeminiServiceError):
            asyncio.run(svc.generate("q", "ctx", on_attempt=lambda: on_attempt_calls.append(1)))

        assert len(attempts) == 3          # 3 outbound attempts
        assert len(on_attempt_calls) == 3  # counter incremented per attempt

    def test_no_retry_on_permanent_error(self, monkeypatch):
        import asyncio
        from app.services.gemini_service import GeminiServiceError

        svc = self._service()
        attempts = []
        on_attempt_calls = []

        async def failing_call(*args, **kwargs):
            attempts.append(1)
            raise GeminiServiceError(
                "invalid_key", "bad key", should_retry=False
            )

        monkeypatch.setattr(svc, "_call_gemini", failing_call)
        monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 3)
        monkeypatch.setattr(settings, "GEMINI_RETRY_BASE_DELAY", 0.01)

        with pytest.raises(GeminiServiceError):
            asyncio.run(svc.generate("q", "ctx", on_attempt=lambda: on_attempt_calls.append(1)))

        assert len(attempts) == 1          # permanent error → no retry
        assert len(on_attempt_calls) == 1

    def test_retry_after_header_respected(self, monkeypatch):
        """A server-provided Retry-After hint overrides computed backoff."""
        import asyncio
        from app.services.gemini_service import GeminiServiceError

        svc = self._service()
        sleeps = []

        real_sleep = asyncio.sleep

        async def fake_sleep(delay, *a, **k):
            sleeps.append(delay)
            await real_sleep(0)

        async def failing_call(*args, **kwargs):
            raise GeminiServiceError(
                "rate_limit", "rate limited", should_retry=True,
                retry_after=7.0,
            )

        monkeypatch.setattr(svc, "_call_gemini", failing_call)
        monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 2)
        monkeypatch.setattr(settings, "GEMINI_RETRY_BASE_DELAY", 0.01)
        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        with pytest.raises(GeminiServiceError):
            asyncio.run(svc.generate("q", "ctx"))

        assert 7.0 in sleeps  # used the server-provided hint

    def test_retry_after_extraction(self):
        from app.services.gemini_service import GeminiService

        class FakeSDKError(Exception):
            retry_delay = 42

        assert GeminiService._extract_retry_after(FakeSDKError("boom")) == 42.0
        assert GeminiService._extract_retry_after(
            Exception("429 TOO_MANY_REQUESTS retry_delay: 36 seconds")
        ) == 36.0
        assert GeminiService._extract_retry_after(Exception("no hints here")) is None

    def test_retired_model_404_not_retried(self):
        """A retired/unavailable model returns 404 NOT_FOUND — a permanent
        client error that must fail fast (single attempt, friendly message)."""
        from app.services.gemini_service import GeminiService, GeminiServiceError

        svc = GeminiService()
        err = Exception(
            "ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': "
            "'This model models/gemini-2.0-flash is no longer available.', "
            "'status': 'NOT_FOUND'}}"
        )
        translated = None
        try:
            svc._translate_api_error(err)
        except GeminiServiceError as e:
            translated = e
        assert translated is not None
        assert translated.should_retry is False
        assert translated.error_type == "bad_request"

    def test_bad_request_400_not_retried(self):
        from app.services.gemini_service import GeminiService, GeminiServiceError

        svc = GeminiService()
        translated = None
        try:
            svc._translate_api_error(Exception(
                "ClientError: 400 INVALID_ARGUMENT. {'status': 'INVALID_ARGUMENT'}"
            ))
        except GeminiServiceError as e:
            translated = e
        assert translated is not None
        assert translated.should_retry is False


class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "database" in data
        assert "documents_loaded" in data
        assert data["documents_loaded"] == 4   # exactly 4 course docs


class TestSessionReset:
    def test_session_reset(self, client):
        resp = client.post("/api/session/reset", json={
            "session_id": "s-resettest1234",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

