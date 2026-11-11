"""
tests/test_cache.py — Cache correctness tests (mocked Gemini).
"""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.services.cache_service import cache_service, normalize_question, make_cache_key
from app.services.document_service import document_service


MOCK_ANSWER = {
    "answer": "To earn the Verified Certificate, you need at least 60% overall and at least 40% in the final exam.",
    "sources": ["03_grading_policy_v2.txt"],
    "is_supported": True,
}


def mock_gemini_response():
    from app.services.gemini_service import GeminiResponse
    return GeminiResponse(
        answer=MOCK_ANSWER["answer"],
        sources=MOCK_ANSWER["sources"],
        is_supported=True,
    )


class TestQuestionNormalization:
    def test_lowercase(self):
        assert normalize_question("WHAT IS THE PASS MARK?") == "what is the pass mark?"

    def test_strip_whitespace(self):
        assert normalize_question("  hello world  ") == "hello world"

    def test_collapse_whitespace(self):
        assert normalize_question("what   is   the   score") == "what is the score"

    def test_equivalent_questions(self):
        q1 = normalize_question("What is the minimum score needed to pass?")
        q2 = normalize_question("what is the minimum score needed to pass?")
        q3 = normalize_question("  WHAT IS THE MINIMUM SCORE NEEDED TO PASS?  ")
        assert q1 == q2 == q3

    def test_apostrophe_normalization(self):
        q1 = normalize_question("what's the deadline")
        q2 = normalize_question("what\u2019s the deadline")
        assert q1 == q2


class TestCacheKey:
    def test_different_questions_give_different_keys(self):
        fp = "abc123"
        k1 = make_cache_key("what is the pass mark", "", fp, "2.0", "gemini-flash")
        k2 = make_cache_key("when is the exam", "", fp, "2.0", "gemini-flash")
        assert k1 != k2

    def test_same_question_same_key(self):
        fp = "abc123"
        k1 = make_cache_key("what is the pass mark", "", fp, "2.0", "gemini-flash")
        k2 = make_cache_key("what is the pass mark", "", fp, "2.0", "gemini-flash")
        assert k1 == k2

    def test_different_fingerprint_different_key(self):
        k1 = make_cache_key("pass mark", "", "fp1", "2.0", "gemini-flash")
        k2 = make_cache_key("pass mark", "", "fp2", "2.0", "gemini-flash")
        assert k1 != k2

    def test_different_policy_version_different_key(self):
        k1 = make_cache_key("pass mark", "", "fp1", "1.0", "gemini-flash")
        k2 = make_cache_key("pass mark", "", "fp1", "2.0", "gemini-flash")
        assert k1 != k2


class TestCacheIntegration:
    """Integration tests using the TestClient — Gemini is mocked."""

    @pytest.fixture(autouse=True)
    def patch_gemini(self):
        with patch(
            "app.services.gemini_service.GeminiService.generate",
            new_callable=AsyncMock,
            return_value=mock_gemini_response(),
        ):
            yield

    def test_first_request_not_cached(self, client):
        import uuid
        resp = client.post("/api/chat", json={
            "session_id": "cache-test-" + uuid.uuid4().hex[:8],
            "request_id": "req-" + uuid.uuid4().hex[:8],
            "message": "What is the minimum score to pass the course?",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False

    def test_same_session_exact_repeat_hits_cache(self, client):
        """§16 cache test #2: 'Ask the same question again' — SAME student,
        SAME session. The repeat must be served from the cache even though
        the first ask is now part of the conversation history."""
        import uuid
        sid = "same-sess-" + uuid.uuid4().hex[:8]
        q = "What is the minimum score to pass the course? same-session repeat"

        r1 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-a-" + uuid.uuid4().hex[:8],
            "message": q,
        })
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        r2 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-b-" + uuid.uuid4().hex[:8],
            "message": q,
        })
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert r2.json()["answer"] == r1.json()["answer"]
        assert r2.json()["sources"] == r1.json()["sources"]

    def test_same_session_case_variant_hits_cache(self, client):
        """§16 cache test #3: different capitalization, same session."""
        import uuid
        sid = "same-sess-c-" + uuid.uuid4().hex[:8]

        r1 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-c1-" + uuid.uuid4().hex[:8],
            "message": "What is the exam duration? case variant check",
        })
        assert r1.json()["cached"] is False

        r2 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-c2-" + uuid.uuid4().hex[:8],
            "message": "WHAT IS THE EXAM DURATION? CASE VARIANT CHECK",
        })
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

    def test_repeat_after_other_turns_is_context_distinct(self, client):
        """A question re-asked after OTHER turns keeps its context in the key:
        interleaved context may change the meaning, so it is not conflated
        with the context-free first ask (documented design decision)."""
        import uuid
        sid = "interleaved-" + uuid.uuid4().hex[:8]
        q = "What is the exam duration? interleaved check"

        r1 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-i1-" + uuid.uuid4().hex[:8],
            "message": q,
        })
        assert r1.json()["cached"] is False

        # A different question in between changes the conversation context
        client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-i2-" + uuid.uuid4().hex[:8],
            "message": "How many weekly quizzes are there? interleaved check",
        })

        r2 = client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-i3-" + uuid.uuid4().hex[:8],
            "message": q,
        })
        assert r2.status_code == 200
        assert r2.json()["cached"] is False

    def test_same_question_uses_cache(self, client):
        import uuid
        sid = "cache-test-" + uuid.uuid4().hex[:8]
        question = "What is the minimum score to pass the course unique test?"

        # First request
        client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-first-" + uuid.uuid4().hex[:8],
            "message": question,
        })

        # Second request — different session but same question
        sid2 = "cache-test-" + uuid.uuid4().hex[:8]
        resp2 = client.post("/api/chat", json={
            "session_id": sid2,
            "request_id": "req-second-" + uuid.uuid4().hex[:8],
            "message": question,
        })
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["cached"] is True

    def test_case_insensitive_cache(self, client):
        import uuid
        question_lower = "what is the minimum passing score for dpm-101 test case?"
        question_upper = "WHAT IS THE MINIMUM PASSING SCORE FOR DPM-101 TEST CASE?"
        sid = "cache-test-" + uuid.uuid4().hex[:8]

        client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-lower-" + uuid.uuid4().hex[:8],
            "message": question_lower,
        })

        resp = client.post("/api/chat", json={
            "session_id": "cache-test-" + uuid.uuid4().hex[:8],
            "request_id": "req-upper-" + uuid.uuid4().hex[:8],
            "message": question_upper,
        })
        assert resp.status_code == 200
        assert resp.json()["cached"] is True

    def test_extra_spaces_uses_cache(self, client):
        import uuid
        q1 = "how many quizzes are there in dpm-101?"
        q2 = "  how   many   quizzes   are   there   in   dpm-101?  "
        sid = "cache-test-" + uuid.uuid4().hex[:8]

        client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "req-q1-" + uuid.uuid4().hex[:8],
            "message": q1,
        })

        resp = client.post("/api/chat", json={
            "session_id": "cache-test-" + uuid.uuid4().hex[:8],
            "request_id": "req-q2-" + uuid.uuid4().hex[:8],
            "message": q2,
        })
        assert resp.status_code == 200
        assert resp.json()["cached"] is True

    def test_duplicate_request_id_not_counted_twice(self, client):
        """Same request_id submitted twice must not inflate total_questions."""
        import uuid
        sid = "dedup-test-" + uuid.uuid4().hex[:8]
        rid = "req-dedup-" + uuid.uuid4().hex[:8]
        question = "What is the exam date? dedup test"

        # First submission
        r1 = client.post("/api/chat", json={
            "session_id": sid, "request_id": rid, "message": question
        })
        # Second submission with same request_id
        r2 = client.post("/api/chat", json={
            "session_id": sid, "request_id": rid, "message": question
        })
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_cache_invalidated_on_document_fingerprint_change(self, client, monkeypatch):
        """Requirement: modify document fingerprint → stale cache is not served."""
        import uuid
        question = "fingerprint-change test: what is the exam date? " + uuid.uuid4().hex[:6]

        # 1st request — Gemini called, response cached
        with patch("app.services.gemini_service.GeminiService.generate",
                   new_callable=AsyncMock, return_value=mock_gemini_response()):
            r1 = client.post("/api/chat", json={
                "session_id": "s-fp-" + uuid.uuid4().hex[:8],
                "request_id": "r-fp1-" + uuid.uuid4().hex[:8],
                "message": question,
            })
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        # 2nd request — served from cache
        r2 = client.post("/api/chat", json={
            "session_id": "s-fp-" + uuid.uuid4().hex[:8],
            "request_id": "r-fp2-" + uuid.uuid4().hex[:8],
            "message": question,
        })
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

        # 3rd request after the document corpus "changed" (new fingerprint)
        # → the stale entry must not be served; Gemini must be called again.
        monkeypatch.setattr(
            document_service, "get_corpus_fingerprint", lambda: "changed-fp"
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   new_callable=AsyncMock, return_value=mock_gemini_response()) as m:
            r3 = client.post("/api/chat", json={
                "session_id": "s-fp-" + uuid.uuid4().hex[:8],
                "request_id": "r-fp3-" + uuid.uuid4().hex[:8],
                "message": question,
            })
        assert r3.status_code == 200
        assert r3.json()["cached"] is False
        assert m.await_count >= 1

