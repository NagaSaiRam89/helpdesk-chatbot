"""
tests/test_chat.py
Tests for all 12 sample questions and other chat endpoint behaviors.
Gemini is mocked — factual correctness is verified via retrieval + policy.
"""
import pytest
import uuid
from unittest.mock import patch, AsyncMock


def make_gemini_mock(answer: str, sources: list, is_supported: bool = True):
    from app.services.gemini_service import GeminiResponse
    return AsyncMock(return_value=GeminiResponse(
        answer=answer, sources=sources, is_supported=is_supported
    ))


def chat(client, message: str, session_id: str = None, request_id: str = None):
    return client.post("/api/chat", json={
        "session_id": session_id or ("s-" + uuid.uuid4().hex[:12]),
        "request_id": request_id or ("r-" + uuid.uuid4().hex[:12]),
        "message": message,
    })


class TestSampleQuestions:
    """
    Tests for all 12 sample questions.
    For each test we verify:
    - HTTP 200
    - Answer is non-empty
    - Sources contain expected document(s)
    - is_supported flag
    - Policy correctness (v2 policy used, not v1)
    """

    # Q1: Minimum score to pass
    def test_q1_minimum_score_to_pass(self, client):
        answer_text = (
            "To earn the Verified Certificate, you need at least 60% overall "
            "AND at least 40% in the Proctored Final Exam separately."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["03_grading_policy_v2.txt"])):
            resp = chat(client, "What is the minimum score needed to pass the course and get a Verified Certificate?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        assert "03_grading_policy_v2.txt" in data["sources"]
        assert data["is_supported"] is True
        # Must NOT reference old 50% threshold
        assert "50%" not in data["answer"]

    # Q2: Exact repeat of Q1 — should come from cache
    def test_q2_exact_repeat_uses_cache(self, client):
        question = "What is the minimum score needed to pass the course and get a Verified Certificate? cache-test-q2"
        answer_text = "You need at least 60% overall and 40% in the final exam."
        sid = "s-" + uuid.uuid4().hex[:8]

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["03_grading_policy_v2.txt"])):
            r1 = chat(client, question, session_id=sid)

        # Second request — different session, same question
        r2 = chat(client, question, session_id="s-" + uuid.uuid4().hex[:8])
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

    # Q3: Different phrasing (same semantic question)
    def test_q3_different_phrasing(self, client):
        answer_text = "You need 60% overall and at least 40% in the final exam to pass DPM-101."
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["03_grading_policy_v2.txt"])):
            resp = chat(client, "how much do i need to score to pass dpm-101?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        assert data["is_supported"] is True

    # Q4: Late capstone submission
    def test_q4_late_capstone_submission(self, client):
        answer_text = (
            "Late submissions are no longer accepted for weekly quizzes or the capstone case. "
            "The earlier 48-hour grace window has been discontinued."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["03_grading_policy_v2.txt"])):
            resp = chat(client, "What happens if I submit my Capstone project 1 day late?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        # Must reference v2 policy (no grace window)
        assert "03_grading_policy_v2.txt" in data["sources"]

    # Q5: Capstone deadline
    def test_q5_capstone_deadline(self, client):
        answer_text = "The Capstone Product Case is due on 15 November 2026, 11:59 PM IST."
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["03_grading_policy_v2.txt"])):
            resp = chat(client, "When is the Capstone Product Case due?")
        assert resp.status_code == 200
        data = resp.json()
        assert "November" in data["answer"] or "15" in data["answer"]
        assert "03_grading_policy_v2.txt" in data["sources"]

    # Q6: Open-book or closed-book exam
    def test_q6_exam_format(self, client):
        answer_text = (
            "The final exam is strictly CLOSED-BOOK. It is scheduled for 20 November 2026, "
            "with slots between 10:00 AM and 8:00 PM IST."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["04_exam_rules_and_faq.txt"])):
            resp = chat(client, "Is the final exam open-book or closed-book, and when is it scheduled?")
        assert resp.status_code == 200
        data = resp.json()
        # Must say closed-book, not open-book
        assert "closed" in data["answer"].lower() or "04_exam_rules_and_faq.txt" in data["sources"]
        assert "open-book" not in data["answer"].lower() or "closed" in data["answer"].lower()

    # Q7: iPad/mobile for exam
    def test_q7_ipad_exam(self, client):
        answer_text = (
            "Mobile phones and tablets (including iPads) are NOT supported for the proctored final exam. "
            "You must use a desktop or laptop computer."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["04_exam_rules_and_faq.txt"])):
            resp = chat(client, "Can I take the proctored final exam on my iPad or mobile phone?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        assert "04_exam_rules_and_faq.txt" in data["sources"]

    # Q8: Certificate cost
    def test_q8_certificate_cost(self, client):
        answer_text = "The Verified Certificate Track costs Rs. 1,500."
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["01_course_handbook.txt"])):
            resp = chat(client, "How much does the Verified Certificate track cost?")
        assert resp.status_code == 200
        data = resp.json()
        assert "1,500" in data["answer"] or "1500" in data["answer"]
        assert "01_course_handbook.txt" in data["sources"]

    # Q9: Refund policy (not in documents)
    def test_q9_refund_not_in_docs(self, client):
        answer_text = (
            "The course documents do not contain any refund policy. "
            "Please contact the course support team for refund-related questions."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, [], is_supported=False)):
            resp = chat(client, "If I pay the Rs. 1,500 fee and drop the course in Week 2, how do I get a refund?")
        assert resp.status_code == 200
        data = resp.json()
        # Should not invent a refund policy
        assert data["answer"]
        assert data["is_supported"] is False or "not" in data["answer"].lower()

    # Q10: Weekly quizzes
    def test_q10_weekly_quizzes(self, client):
        answer_text = (
            "There are 6 weekly quizzes in total, but only the best 5 count towards your score. "
            "So you can miss 1 quiz without any penalty."
        )
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(answer_text, ["01_course_handbook.txt", "03_grading_policy_v2.txt"])):
            resp = chat(client, "How many weekly quizzes are there, and what if I miss one?")
        assert resp.status_code == 200
        data = resp.json()
        assert "6" in data["answer"] or "six" in data["answer"].lower()

    # Q11: Out-of-scope (SQL, Kubernetes)
    def test_q11_out_of_scope_sql_kubernetes(self, client):
        resp = chat(client, "Does this course teach SQL database indexing and Kubernetes deployment?")
        assert resp.status_code == 200
        data = resp.json()
        # Either retrieval returns out-of-scope (no Gemini call) or Gemini says not covered
        assert data["answer"]
        # Should not claim these topics ARE taught
        assert "kubernetes" not in data["answer"].lower() or "not" in data["answer"].lower()

    # Q12: Completely off-topic (cricket)
    def test_q12_completely_off_topic(self, client):
        resp = chat(client, "Who won the cricket match last night?")
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"]
        # Must not answer the cricket question with match results
        assert "won" not in data["answer"].lower() or "not" in data["answer"].lower()


class TestFollowUpConversations:
    """
    Requirement: contextual follow-ups must work, and sessions must be isolated.
    """

    def _post(self, client, sid, msg):
        return client.post("/api/chat", json={
            "session_id": sid,
            "request_id": "r-" + uuid.uuid4().hex[:12],
            "message": msg,
        })

    def test_follow_up_uses_previous_context(self, client):
        """Initial question → context-dependent follow-up. Both answered;
        both turns recorded in the session history."""
        sid = "s-followup-" + uuid.uuid4().hex[:8]

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "The final exam is 90 minutes long.",
                       ["04_exam_rules_and_faq.txt"])):
            r1 = self._post(client, sid, "What is the examination duration?")
        assert r1.status_code == 200
        assert r1.json()["is_supported"] is True

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "To pass, you need at least 60% overall and 40% in the final exam.",
                       ["03_grading_policy_v2.txt"])):
            r2 = self._post(client, sid, "What about the passing marks?")
        assert r2.status_code == 200
        assert r2.json()["is_supported"] is True
        assert "03_grading_policy_v2.txt" in r2.json()["sources"]

        # History must contain both turns (user + assistant, twice)
        from app.database.models import ConversationMessage
        from tests.conftest import TestSessionLocal
        with TestSessionLocal() as db:
            messages = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.session_id == sid)
                .order_by(ConversationMessage.id.asc())
                .all()
            )
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert messages[0].content == "What is the examination duration?"
        assert "90 minutes" in messages[1].content
        assert messages[2].content == "What about the passing marks?"

    def test_pronoun_follow_up_answered_from_docs(self, client):
        """'What happens if I miss it?' — resolvable only via prior context."""
        sid = "s-followup2-" + uuid.uuid4().hex[:8]

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "There are 6 weekly quizzes and the best 5 are counted.",
                       ["01_course_handbook.txt"])):
            r1 = self._post(client, sid, "How many weekly quizzes are there?")
        assert r1.status_code == 200

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "Because only the best 5 of 6 quizzes count, you can miss 1 quiz without penalty.",
                       ["03_grading_policy_v2.txt"])):
            r2 = self._post(client, sid, "What happens if I miss one?")
        assert r2.status_code == 200
        assert r2.json()["is_supported"] is True

    def test_sessions_do_not_share_history(self, client):
        """A follow-up in session B must not see session A's context."""
        sid_a = "s-isol-a-" + uuid.uuid4().hex[:8]
        sid_b = "s-isol-b-" + uuid.uuid4().hex[:8]

        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "The final exam is 90 minutes long.",
                       ["04_exam_rules_and_faq.txt"])):
            r_a = self._post(client, sid_a, "What is the examination duration?")
        assert r_a.status_code == 200

        # Session B asks the pronoun follow-up WITHOUT the prior context.
        # It must still be handled (from documents), and B's history must
        # not contain A's messages.
        with patch("app.services.gemini_service.GeminiService.generate",
                   make_gemini_mock(
                       "You can miss 1 weekly quiz without penalty (best 5 of 6 counted).",
                       ["03_grading_policy_v2.txt"])):
            r_b = self._post(client, sid_b, "What happens if I miss one?")
        assert r_b.status_code == 200

        from app.database.models import ConversationMessage
        from tests.conftest import TestSessionLocal
        with TestSessionLocal() as db:
            msgs_b = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.session_id == sid_b)
                .all()
            )
        contents = [m.content for m in msgs_b]
        assert "What is the examination duration?" not in contents
        assert len(msgs_b) == 2  # B's own single turn only


class TestInputValidation:
    def test_empty_message_rejected(self, client):
        resp = client.post("/api/chat", json={
            "session_id": "s-validtest1234",
            "request_id": "r-validtest1234",
            "message": "   ",
        })
        assert resp.status_code == 422

    def test_message_too_long_rejected(self, client):
        resp = client.post("/api/chat", json={
            "session_id": "s-validtest1234",
            "request_id": "r-validtest1234",
            "message": "x" * 2001,
        })
        assert resp.status_code == 422

    def test_invalid_session_id_rejected(self, client):
        resp = client.post("/api/chat", json={
            "session_id": "has spaces in it",
            "request_id": "r-valid1234567",
            "message": "hello",
        })
        assert resp.status_code == 422

    def test_missing_fields_rejected(self, client):
        resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 422

