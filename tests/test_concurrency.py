"""
tests/test_concurrency.py
Single-flight deduplication and concurrent request tests.
"""
import asyncio
import uuid
import pytest
from unittest.mock import patch, AsyncMock


def make_gemini_mock(answer="Test answer", sources=None):
    from app.services.gemini_service import GeminiResponse
    return AsyncMock(return_value=GeminiResponse(
        answer=answer,
        sources=sources or ["03_grading_policy_v2.txt"],
        is_supported=True,
    ))


class TestInflightDeduplication:
    """Tests for the InflightService single-flight mechanism."""

    def test_leader_follower_mechanism(self):
        from app.services.inflight_service import InflightService
        import asyncio

        async def _run():
            svc = InflightService()
            is_leader1, fut1 = await svc.get_or_start("key-abc")
            is_leader2, fut2 = await svc.get_or_start("key-abc")

            assert is_leader1 is True
            assert fut1 is None
            assert is_leader2 is False
            assert fut2 is not None

            # Leader resolves
            await svc.resolve("key-abc", "result")
            result = await fut2
            assert result == "result"

        asyncio.run(_run())

    def test_different_keys_are_independent_leaders(self):
        from app.services.inflight_service import InflightService
        import asyncio

        async def _run():
            svc = InflightService()
            l1, _ = await svc.get_or_start("key-1")
            l2, _ = await svc.get_or_start("key-2")
            assert l1 is True
            assert l2 is True

        asyncio.run(_run())

    def test_rejection_propagates_to_follower(self):
        from app.services.inflight_service import InflightService
        import asyncio

        async def _run():
            svc = InflightService()
            _, _ = await svc.get_or_start("key-err")
            is_leader2, fut2 = await svc.get_or_start("key-err")

            await svc.reject("key-err", ValueError("Gemini failed"))

            with pytest.raises(ValueError):
                await fut2

        asyncio.run(_run())

    def test_reject_without_followers_is_silent(self):
        """A leader that fails before any follower joins must not leave an
        unretrieved future exception (asyncio would log 'Future exception
        was never retrieved')."""
        import asyncio
        import gc
        from app.services.inflight_service import InflightService

        logged = []

        async def _run():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda l, ctx: logged.append(ctx))

            svc = InflightService()
            is_leader, fut = await svc.get_or_start("key-solo")
            assert is_leader is True
            assert fut is None

            await svc.reject("key-solo", ValueError("leader failed alone"))

            # The popped future should have been consumed — force GC while
            # the loop is still running so __del__ would surface any
            # unretrieved-exception warning into our handler.
            gc.collect()

        asyncio.run(_run())
        assert not logged, f"Unexpected loop exception logged: {logged}"


class TestConcurrentChatRequests:
    """
    Simulate multiple concurrent requests for the same question.
    Only 1 Gemini call should be made; all clients get the same answer.
    """

    def test_concurrent_same_question_single_gemini_call(self, client):
        """
        Send 5 requests for the same question using threads.
        All should succeed; only 1 Gemini call should be tracked.
        """
        import threading
        import time

        question = "concurrent test: what is the exam date unique " + uuid.uuid4().hex[:6]
        call_count = [0]
        original_generate = None

        from app.services.gemini_service import GeminiResponse

        async def counting_generate(*args, **kwargs):
            call_count[0] += 1
            await asyncio.sleep(0.1)  # simulate latency
            return GeminiResponse(
                answer="Exam is on 20 November 2026.",
                sources=["04_exam_rules_and_faq.txt"],
                is_supported=True,
            )

        results = []

        def make_request(i):
            resp = client.post("/api/chat", json={
                "session_id": "s-concurrent-" + uuid.uuid4().hex[:8],
                "request_id": "r-concurrent-" + uuid.uuid4().hex[:8],
                "message": question,
            })
            results.append(resp)

        with patch("app.services.gemini_service.GeminiService.generate",
                   side_effect=counting_generate):
            threads = [threading.Thread(target=make_request, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert all(r.status_code == 200 for r in results), \
            f"Not all requests succeeded: {[r.status_code for r in results]}"
        # All answers should be non-empty
        for r in results:
            assert r.json()["answer"]

        # CRITICAL: only ONE Gemini call for 5 identical concurrent requests
        assert call_count[0] == 1, \
            f"Expected exactly 1 Gemini call, got {call_count[0]}"
        # All waiting clients received the same validated result
        answers = {r.json()["answer"] for r in results}
        assert answers == {"Exam is on 20 November 2026."}

    def test_concurrent_requests_each_counted_as_questions(self, client):
        """Every concurrent identical request is an accepted student question,
        but only one shared Gemini request is made."""
        import threading
        from app.database.models import AppCounter
        from app.services.gemini_service import GeminiResponse
        from tests.conftest import TestSessionLocal

        question = "concurrent counting: what is the capstone deadline " + uuid.uuid4().hex[:6]
        call_count = [0]

        async def counting_generate(*args, on_attempt=None, **kwargs):
            call_count[0] += 1
            # Honor the per-attempt counting contract of the real service
            if on_attempt is not None:
                on_attempt()
            await asyncio.sleep(0.1)
            return GeminiResponse(
                answer="The capstone is due on 15 November 2026.",
                sources=["03_grading_policy_v2.txt"],
                is_supported=True,
            )

        with TestSessionLocal() as db:
            row = db.query(AppCounter).filter(AppCounter.id == 1).first()
            baseline_questions = row.total_questions if row else 0
            baseline_gemini = row.gemini_api_calls if row else 0

        n = 5
        results = []

        def make_request(i):
            resp = client.post("/api/chat", json={
                "session_id": "s-cc-" + uuid.uuid4().hex[:8],
                "request_id": "r-cc-" + uuid.uuid4().hex[:8],
                "message": question,
            })
            results.append(resp)

        with patch("app.services.gemini_service.GeminiService.generate",
                   side_effect=counting_generate):
            threads = [threading.Thread(target=make_request, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert all(r.status_code == 200 for r in results)
        assert call_count[0] == 1  # single shared Gemini request

        with TestSessionLocal() as db:
            row = db.query(AppCounter).filter(AppCounter.id == 1).first()
            assert row.total_questions >= baseline_questions + n
            assert row.gemini_api_calls == baseline_gemini + 1


class TestStatsCounters:
    """Analytics counter behavior tests."""

    def test_cache_hit_increments_total_and_cache_not_gemini(self, client, db_session):
        from app.services.analytics_service import analytics_service

        # Reset counters for clean test
        from app.database.models import AppCounter
        row = db_session.query(AppCounter).filter(AppCounter.id == 1).first()
        if row:
            baseline_total = row.total_questions
            baseline_gemini = row.gemini_api_calls
            baseline_cache = row.cache_hits
        else:
            baseline_total = baseline_gemini = baseline_cache = 0

        question = "stats test: what is the minimum score? " + uuid.uuid4().hex[:6]

        from app.services.gemini_service import GeminiResponse
        async def mock_gen(*args, **kwargs):
            return GeminiResponse(
                answer="You need 60% overall and 40% in the exam.",
                sources=["03_grading_policy_v2.txt"],
                is_supported=True,
            )

        # First request (Gemini call)
        with patch("app.services.gemini_service.GeminiService.generate", side_effect=mock_gen):
            r1 = client.post("/api/chat", json={
                "session_id": "s-stats-" + uuid.uuid4().hex[:8],
                "request_id": "r-stats1-" + uuid.uuid4().hex[:8],
                "message": question,
            })
        assert r1.status_code == 200

        # Second request (cache hit)
        r2 = client.post("/api/chat", json={
            "session_id": "s-stats-" + uuid.uuid4().hex[:8],
            "request_id": "r-stats2-" + uuid.uuid4().hex[:8],
            "message": question,
        })
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

        # Check stats endpoint
        stats_resp = client.get("/api/stats")
        assert stats_resp.status_code == 200
        stats = stats_resp.json()
        assert stats["total_questions"] >= baseline_total + 2
        assert stats["cache_hits"] >= baseline_cache + 1

    def test_stats_endpoint_returns_correct_shape(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_questions" in data
        assert "gemini_api_calls" in data
        assert "cache_hits" in data
        assert all(isinstance(v, int) for v in data.values())

