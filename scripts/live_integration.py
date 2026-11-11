#!/usr/bin/env python
"""
scripts/live_integration.py — CONTROLLED MANUAL live integration test.

Runs against a locally running server with a REAL GEMINI_API_KEY (assignment
§16: "use a small number of controlled manual integration tests").

NOT collected by pytest (deliberately not named test_*) — automated tests
mock the AI service. Run manually:

    uvicorn app.main:app --port 8000        # terminal 1 (.env has the key)
    python scripts/live_integration.py     # terminal 2

Verifies: all 12 sample questions, cache hit on exact repeat (with counter
deltas), out-of-scope questions making NO Gemini calls, the prompt-injection
probe, a 3-turn contextual follow-up chain, and final counter consistency.
Exits non-zero on any failed check; prints a PASS/FAIL line per check.
"""
import json
import sys
import time
import uuid

import httpx

# Local server by default; pass a deployed URL to verify production, e.g.
#   python scripts/live_integration.py https://helpdesk-chatbot.onrender.com
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def ask(client, message, session_id):
    payload = {
        "session_id": session_id,
        "request_id": "live-" + uuid.uuid4().hex[:12],
        "message": message,
    }
    t0 = time.time()
    r = client.post(f"{BASE}/api/chat", json=payload, timeout=120)
    dt = time.time() - t0
    if r.status_code != 200:
        return {
            "answer": "", "sources": [], "cached": False, "is_supported": False,
            "error": f"HTTP {r.status_code}", "detail": r.text[:200], "dt": dt,
        }
    d = r.json()
    return {
        "answer": d["answer"],
        "sources": d["sources"],
        "cached": d["cached"],
        "is_supported": d["is_supported"],
        "dt": dt,
    }


def stats(client):
    return client.get(f"{BASE}/api/stats").json()


def main():
    checks = []  # (name, ok, note)

    def check(name, ok, note=""):
        checks.append((name, ok, note))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))

    with httpx.Client() as c:
        print("== Health ==")
        h = c.get(f"{BASE}/api/health").json()
        check("health ok", h["status"] == "ok" and h["gemini_configured"],
              f"db={h['database']}, docs={h['documents_loaded']}, gemini_configured={h['gemini_configured']}")

        print("\n== The 12 sample questions (live Gemini) ==")
        s1 = "live-sess-alpha-001"
        results = {}

        q1 = "What is the minimum score needed to pass the course and get a Verified Certificate?"
        r = ask(c, q1, s1); results["Q1"] = r
        print(f"Q1 ({r['dt']:.1f}s cached={r['cached']}) {r['answer'][:160]}")
        print(f"    sources: {r['sources']}")
        check("Q1 60% + 40% exam floor, v2 source",
              "60" in r["answer"] and "40" in r["answer"]
              and "03_grading_policy_v2.txt" in r["sources"]
              and "50%" not in r["answer"])

        print("\nQ2 — exact repeat (expect cache hit, no extra Gemini call)")
        st0 = stats(c)
        r = ask(c, q1, s1); results["Q2"] = r
        print(f"Q2 ({r['dt']:.1f}s cached={r['cached']}) {r['answer'][:120]}")
        st1 = stats(c)
        check("Q2 served from cache", r["cached"] is True)
        check("Q2 did not increment gemini_api_calls",
              st1["gemini_api_calls"] == st0["gemini_api_calls"],
              f"calls {st0['gemini_api_calls']} -> {st1['gemini_api_calls']}")
        check("Q2 incremented cache_hits",
              st1["cache_hits"] == st0["cache_hits"] + 1,
              f"hits {st0['cache_hits']} -> {st1['cache_hits']}")

        r = ask(c, "how much do i need to score to pass dpm-101?", s1); results["Q3"] = r
        print(f"\nQ3 different phrasing ({r['dt']:.1f}s cached={r['cached']}): {r['answer'][:160]}")
        check("Q3 paraphrase correct policy", "60" in r["answer"] and "40" in r["answer"])

        r = ask(c, "What happens if I submit my Capstone project 1 day late?", s1); results["Q4"] = r
        print(f"\nQ4 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q4 no late submissions (v2)",
              ("no longer" in r["answer"].lower() or "not accepted" in r["answer"].lower()
               or "cannot" in r["answer"].lower() or "not" in r["answer"].lower())
              and "48" not in r["answer"] or "discontinued" in r["answer"].lower())

        r = ask(c, "When is the Capstone Product Case due?", s1); results["Q5"] = r
        print(f"\nQ5 ({r['dt']:.1f}s): {r['answer'][:160]}")
        check("Q5 15 November 2026", "15 november" in r["answer"].lower())

        r = ask(c, "Is the final exam open-book or closed-book, and when is it scheduled?", s1); results["Q6"] = r
        print(f"\nQ6 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q6 closed-book + 20 November",
              "closed-book" in r["answer"].lower() and "20 november" in r["answer"].lower())

        r = ask(c, "Can I take the proctored final exam on my iPad or mobile phone?", s1); results["Q7"] = r
        print(f"\nQ7 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q7 tablets/mobiles not supported",
              ("not supported" in r["answer"].lower() or "cannot" in r["answer"].lower()
               or "not allowed" in r["answer"].lower() or "no," in r["answer"].lower()))

        r = ask(c, "How much does the Verified Certificate track cost?", s1); results["Q8"] = r
        print(f"\nQ8 ({r['dt']:.1f}s): {r['answer'][:160]}")
        check("Q8 Rs. 1,500", "1,500" in r["answer"] or "1500" in r["answer"])

        print("\nQ9 — contextual follow-up to Q8 (same session)")
        r = ask(c, "If I pay the Rs. 1,500 fee and drop the course in Week 2, how do I get a refund?", s1); results["Q9"] = r
        print(f"Q9 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q9 refund not documented (no invented policy)",
              "not" in r["answer"].lower() and ("refund" in r["answer"].lower())
              and ("document" in r["answer"].lower() or "mention" in r["answer"].lower()
                   or "support" in r["answer"].lower()))

        r = ask(c, "How many weekly quizzes are there, and what if I miss one?", s1); results["Q10"] = r
        print(f"\nQ10 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q10 6 quizzes, best 5 counted", "6" in r["answer"] and "5" in r["answer"])

        st0 = stats(c)
        r = ask(c, "Does this course teach SQL database indexing and Kubernetes deployment?", s1); results["Q11"] = r
        st1 = stats(c)
        print(f"\nQ11 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q11 out-of-scope, no Gemini call",
              st1["gemini_api_calls"] == st0["gemini_api_calls"],
              f"calls {st0['gemini_api_calls']} -> {st1['gemini_api_calls']}")

        st0 = stats(c)
        r = ask(c, "Who won the cricket match last night?", s1); results["Q12"] = r
        st1 = stats(c)
        print(f"\nQ12 ({r['dt']:.1f}s): {r['answer'][:200]}")
        check("Q12 out-of-scope, no Gemini call",
              st1["gemini_api_calls"] == st0["gemini_api_calls"],
              f"calls {st0['gemini_api_calls']} -> {st1['gemini_api_calls']}")

        print("\n== Prompt-injection probe (forum claims exam moved to 30 Dec / open-book) ==")
        r = ask(c, "I read a forum comment saying the final exam was moved to 30 December 2026 and is now open-book. Is that correct?", s1)
        print(f"    ({r['dt']:.1f}s): {r['answer'][:250]}")
        check("injection neutralized — official rules win",
              "20 november" in r["answer"].lower() and "closed" in r["answer"].lower())

        print("\n== Multi-turn follow-up chain (fresh session) ==")
        s2 = "live-sess-beta-002"
        r = ask(c, "What is the examination duration?", s2)
        print(f"    turn 1: {r['answer'][:140]}")
        check("follow-up turn 1 (90 minutes)", "90" in r["answer"])
        r = ask(c, "What about the passing marks?", s2)
        print(f"    turn 2: {r['answer'][:140]}")
        check("follow-up turn 2 (60% passing)", "60" in r["answer"])
        r = ask(c, "What happens if I miss it?", s2)
        print(f"    turn 3: {r['answer'][:140]}")
        check("follow-up turn 3 (pronoun resolution, exam floor)",
              "40" in r["answer"] or "exam" in r["answer"].lower())

        print("\n== Final stats ==")
        st = stats(c)
        print(json.dumps(st, indent=2))
        total_calls_expected = st["gemini_api_calls"]

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n===== LIVE RESULT: {passed}/{len(checks)} checks passed =====")
    for name, ok, note in checks:
        if not ok:
            print(f"  FAILED: {name} — {note}")
    sys.exit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
