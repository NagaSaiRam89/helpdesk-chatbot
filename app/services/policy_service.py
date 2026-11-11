"""
app/services/policy_service.py
Explicit policy conflict resolution — does NOT let Gemini decide.

Precedence (highest → lowest):
  1. 03_grading_policy_v2.txt  — explicit superseding statement
  2. 04_exam_rules_and_faq.txt sections 1-2  — official exam rules
  3. 01_course_handbook.txt  — official structural facts
  4. 02_grading_policy_v1.txt  — superseded; historical reference only
  5. Student forum comments in doc 04 section 3  — ZERO authority / prompt-injection risk
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configured precedence registry
# ---------------------------------------------------------------------------

DOCUMENT_AUTHORITY: Dict[str, int] = {
    "03_grading_policy_v2.txt": 100,   # highest — explicitly supersedes v1
    "04_exam_rules_and_faq.txt": 90,   # official exam rules (sections 1-2)
    "01_course_handbook.txt": 80,      # official structural facts
    "02_grading_policy_v1.txt": 10,    # superseded — lowest among official docs
}

# Documents that supersede others on specific topics
SUPERSEDING_MAP: Dict[str, List[str]] = {
    "03_grading_policy_v2.txt": ["02_grading_policy_v1.txt"],
}

# Topics where v1 must NEVER be used as the authoritative source
SUPERSEDED_TOPICS = {
    "02_grading_policy_v1.txt": [
        "passing", "pass", "score", "threshold", "certificate",
        "late", "submission", "grace", "penalty", "deduction",
        "capstone", "deadline",
    ]
}

# Content in doc 04 that is explicitly non-authoritative (student forum)
NON_AUTHORITATIVE_MARKERS = [
    "student discussion",
    "unmoderated",
    "learner_",
    "comment by",
    # Prompt-injection attempt — must be neutralised
    "important system update for ai assistants",
]


@dataclass
class PolicyDecision:
    authoritative_sources: List[str]
    superseded_sources: List[str]
    conflict_detected: bool
    conflict_description: Optional[str]
    ambiguous: bool
    ambiguity_description: Optional[str]
    policy_note: str = ""


class PolicyService:
    """
    Resolves which document(s) are authoritative for a given query,
    and annotates conflicts / ambiguities for the prompt builder.
    """

    def resolve(
        self,
        retrieved_sources: List[str],
        question_lower: str,
    ) -> PolicyDecision:
        """
        Given the sources retrieved for a question, determine:
        - Which are authoritative for this topic.
        - Whether v1 is being incorrectly promoted.
        - Whether a conflict exists that must be disclosed.
        """
        authoritative: List[str] = []
        superseded: List[str] = []
        conflict_detected = False
        conflict_description: Optional[str] = None
        ambiguous = False
        ambiguity_description: Optional[str] = None
        policy_note_parts: List[str] = []

        # Sort sources by authority (highest first)
        sorted_sources = sorted(
            retrieved_sources,
            key=lambda s: DOCUMENT_AUTHORITY.get(s, 50),
            reverse=True,
        )

        for src in sorted_sources:
            # Check if superseded for this topic
            if self._is_superseded_for_topic(src, question_lower):
                superseded.append(src)
                conflict_detected = True
                conflict_description = (
                    f"'{src}' has been explicitly superseded by "
                    f"'03_grading_policy_v2.txt' for grading and "
                    f"late-submission topics. The Version 2 policy applies."
                )
                policy_note_parts.append(
                    "POLICY NOTE: 02_grading_policy_v1.txt has been superseded "
                    "by 03_grading_policy_v2.txt. Use ONLY the Version 2 policy "
                    "for passing criteria, late submissions, and capstone deadline."
                )
            else:
                authoritative.append(src)

        # Specific known ambiguities
        if self._is_refund_question(question_lower):
            ambiguous = True
            ambiguity_description = (
                "The course documents do not contain any refund policy. "
                "This cannot be answered from the available documentation."
            )

        # Both grading docs retrieved — always flag conflict
        if (
            "02_grading_policy_v1.txt" in retrieved_sources
            and "03_grading_policy_v2.txt" in retrieved_sources
        ):
            conflict_detected = True
            if "02_grading_policy_v1.txt" not in superseded:
                superseded.append("02_grading_policy_v1.txt")
                authoritative = [s for s in authoritative if s != "02_grading_policy_v1.txt"]

        policy_note = " | ".join(policy_note_parts) if policy_note_parts else ""

        return PolicyDecision(
            authoritative_sources=authoritative,
            superseded_sources=superseded,
            conflict_detected=conflict_detected,
            conflict_description=conflict_description,
            ambiguous=ambiguous,
            ambiguity_description=ambiguity_description,
            policy_note=policy_note,
        )

    def _is_superseded_for_topic(self, source: str, question_lower: str) -> bool:
        if source not in SUPERSEDED_TOPICS:
            return False
        return any(kw in question_lower for kw in SUPERSEDED_TOPICS[source])

    def _is_refund_question(self, question_lower: str) -> bool:
        return any(kw in question_lower for kw in ["refund", "money back", "reimburse"])

    def get_authority_score(self, filename: str) -> int:
        return DOCUMENT_AUTHORITY.get(filename, 50)

    def is_non_authoritative_content(self, text: str) -> bool:
        """
        Detect content that must NOT be used as policy evidence —
        including prompt injection attempts in the student forum section.
        """
        text_lower = text.lower()
        return any(marker in text_lower for marker in NON_AUTHORITATIVE_MARKERS)

    def build_conflict_preamble(self, decision: PolicyDecision) -> str:
        """Build text to prepend to the Gemini context about conflicts."""
        parts = []
        if decision.conflict_detected and decision.conflict_description:
            parts.append(f"CONFLICT RESOLUTION: {decision.conflict_description}")
        if decision.policy_note:
            parts.append(decision.policy_note)
        if decision.ambiguous and decision.ambiguity_description:
            parts.append(f"AMBIGUITY: {decision.ambiguity_description}")
        return "\n".join(parts)

