"""
app/services/retrieval_service.py
BM25-based retrieval engine with policy-aware filtering.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.config import get_settings
from app.services.document_service import DocumentChunk, document_service
from app.services.policy_service import PolicyService, PolicyDecision

logger = logging.getLogger(__name__)
settings = get_settings()

policy_service = PolicyService()


@dataclass
class RetrievalResult:
    chunk: DocumentChunk
    score: float
    rank: int


@dataclass
class RetrievalResponse:
    results: List[RetrievalResult]
    sources: List[str]          # unique filenames of authoritative chunks
    policy_decision: PolicyDecision
    has_evidence: bool
    out_of_scope: bool
    context_text: str           # assembled prompt context


class BM25Index:
    """
    Minimal BM25 implementation — no external dependencies.
    Parameters: k1=1.5, b=0.75 (standard defaults).
    """

    K1 = 1.5
    B = 0.75

    def __init__(self):
        self._chunks: List[DocumentChunk] = []
        self._tf: List[Dict[str, int]] = []
        self._df: Dict[str, int] = {}
        self._doc_lengths: List[int] = []
        self._avg_dl: float = 0.0
        self._n: int = 0

    def build(self, chunks: List[DocumentChunk]) -> None:
        self._chunks = chunks
        self._tf = []
        self._df = {}
        self._doc_lengths = []

        for chunk in chunks:
            tokens = self._tokenize(chunk.text + " " + chunk.section_heading)
            tf = Counter(tokens)
            self._tf.append(dict(tf))
            self._doc_lengths.append(len(tokens))
            for term in set(tokens):
                self._df[term] = self._df.get(term, 0) + 1

        self._n = len(chunks)
        self._avg_dl = sum(self._doc_lengths) / self._n if self._n else 1.0
        logger.debug("BM25 index built: %d documents", self._n)

    @property
    def vocabulary(self) -> set:
        """All distinct indexed terms — used for the out-of-scope gate."""
        return set(self._df.keys())

    def search(self, query: str, top_k: int = 10) -> List[Tuple[DocumentChunk, float]]:
        if not self._chunks:
            return []

        query_tokens = self._tokenize(query)
        scores: List[float] = [0.0] * self._n

        for term in query_tokens:
            if term not in self._df:
                continue
            idf = math.log(
                (self._n - self._df[term] + 0.5) / (self._df[term] + 0.5) + 1
            )
            for i, (tf_dict, dl) in enumerate(zip(self._tf, self._doc_lengths)):
                tf_val = tf_dict.get(term, 0)
                numerator = tf_val * (self.K1 + 1)
                denominator = tf_val + self.K1 * (
                    1 - self.B + self.B * dl / self._avg_dl
                )
                scores[i] += idf * (numerator / denominator) if denominator else 0

        # Pair with chunks, sort, return top_k
        ranked = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:top_k]
        return [(self._chunks[i], s) for i, s in ranked if s > 0]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = text.lower()
        # keep alphanumeric and % signs (for scores)
        tokens = re.findall(r"[a-z0-9]+(?:%)?", text)
        # Remove very common stop words that add noise
        STOP = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "to", "of", "in", "on", "at", "for", "with", "by", "from",
            "or", "and", "but", "if", "as", "that", "this", "it", "its",
            "not", "no", "any", "all", "each", "per", "than",
        }
        return [t for t in tokens if t not in STOP and len(t) > 1]


# Singleton index — built once at startup
_bm25_index = BM25Index()
_index_built = False


def build_index() -> None:
    global _index_built
    chunks = document_service.get_all_chunks()
    # Only index authoritative chunks
    auth_chunks = [c for c in chunks if c.is_authoritative]
    _bm25_index.build(auth_chunks)
    _index_built = True
    logger.info("BM25 index built with %d authoritative chunks", len(auth_chunks))


# Non-content words ignored by the out-of-scope coverage gate (in addition
# to the BM25 stop list). Interrogatives, auxiliaries and pronouns carry no
# topical signal, so they must not shrink the coverage ratio.
GATE_FILLER_TERMS = {
    "what", "when", "where", "who", "why", "how", "which", "whose", "whom",
    "happen", "happens", "happened",
    "get", "gets", "got",
    "need", "needed", "needs",
    "want", "wants", "wanted",
    "much", "many", "there", "here", "about",
    "please", "tell", "know", "like",
    "thing", "things", "stuff",
    "actually", "really", "just", "also",
    "something", "anything", "everything",
    "can", "cannot", "cant",
    "my", "me", "you", "your", "we", "us", "our",
    "they", "them", "their", "he", "she", "his", "her",
    "it", "its", "one", "that", "this", "these", "those",
}

# A question with at most this many contentful terms is treated as a possible
# contextual follow-up for the out-of-scope gate: if its own terms are mostly
# unknown but the surrounding conversation is clearly on-topic, the preceding
# context may supply the missing referent ("What happens if I miss it?").
FOLLOWUP_RELAXATION_MAX_TERMS = 3


class RetrievalService:

    @staticmethod
    def _term_covered(term: str, vocabulary: set) -> bool:
        """True if a query term exists in the corpus vocabulary.
        Tolerates a trailing 's' (no full stemmer — dataset is tiny and explainability matters).
        """
        if term in vocabulary:
            return True
        return len(term) > 3 and term.endswith("s") and term[:-1] in vocabulary

    @staticmethod
    def _contentful_terms(text: str) -> set:
        """Distinct tokens of `text` that carry topical signal."""
        return set(BM25Index._tokenize(text)) - GATE_FILLER_TERMS

    def _coverage(self, terms: set, vocabulary: set) -> float:
        """Fraction of `terms` that exist in the corpus vocabulary."""
        if not terms:
            return 1.0
        covered = sum(1 for t in terms if self._term_covered(t, vocabulary))
        return covered / len(terms)

    def _out_of_scope_response(self, question: str) -> "RetrievalResponse":
        return RetrievalResponse(
            results=[],
            sources=[],
            policy_decision=policy_service.resolve([], question.lower()),
            has_evidence=False,
            out_of_scope=True,
            context_text="",
        )

    def retrieve(
        self,
        question: str,
        conversation_context: Optional[str] = None,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> RetrievalResponse:
        """
        Retrieve relevant chunks for a question, apply policy resolution,
        and assemble context for Gemini.

        The question is the primary retrieval query; conversation context is
        only used as a fallback when the question alone has no lexical anchor
        (anaphoric follow-ups). Feeding full context into BM25 makes prior
        turns drown out the current question and retrieve the wrong chunks.
        """
        if not _index_built:
            build_index()

        k = top_k or settings.RETRIEVAL_TOP_K
        threshold = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE

        vocabulary = _bm25_index.vocabulary

        # ── Out-of-scope gate (question-first) ────────────────────────────────
        # A question is answerable only if a meaningful fraction of its own
        # contentful terms exist anywhere in the corpus. Pure-BM25 scores let
        # a single common word ("course", "score") push unrelated questions
        # past the score threshold, so this gate prevents spurious Gemini
        # calls. Conversation context is NOT counted here — a long course
        # conversation must not make an off-topic question look in-scope.
        q_terms = self._contentful_terms(question)
        if q_terms and self._coverage(q_terms, vocabulary) < settings.RETRIEVAL_MIN_TERM_COVERAGE:
            # Relaxation for likely follow-ups: short questions whose missing
            # referent the preceding conversation may supply, provided that
            # conversation is itself on-topic.
            ctx_terms = self._contentful_terms(conversation_context) if conversation_context else set()
            likely_followup = (
                len(q_terms) <= FOLLOWUP_RELAXATION_MAX_TERMS
                and bool(ctx_terms)
                and self._coverage(ctx_terms, vocabulary) >= settings.RETRIEVAL_MIN_TERM_COVERAGE
            )
            if not likely_followup:
                logger.debug(
                    "Out-of-scope: term coverage below %.2f for %r",
                    settings.RETRIEVAL_MIN_TERM_COVERAGE, question[:60],
                )
                return self._out_of_scope_response(question)

        # ── Primary retrieval: the question alone ─────────────────────────────
        raw_results = _bm25_index.search(question, top_k=k * 3)  # over-fetch, then filter
        filtered = [
            RetrievalResult(chunk=c, score=s, rank=i + 1)
            for i, (c, s) in enumerate(raw_results)
            if s >= threshold
        ][:k]

        # ── Follow-up fallback ────────────────────────────────────────────────
        # If the question alone has no strong lexical anchor (anaphoric
        # follow-ups such as "What happens if I miss it?"), retry with the
        # conversation context prepended so the referent's terms guide BM25.
        if conversation_context and (
            not filtered or filtered[0].score < settings.RETRIEVAL_ANCHOR_MIN_SCORE
        ):
            expanded_query = f"{conversation_context}\n{question}"
            raw_expanded = _bm25_index.search(expanded_query, top_k=k * 3)
            expanded = [
                RetrievalResult(chunk=c, score=s, rank=i + 1)
                for i, (c, s) in enumerate(raw_expanded)
                if s >= threshold
            ][:k]
            if expanded and (not filtered or expanded[0].score > filtered[0].score):
                filtered = expanded

        if not filtered:
            return RetrievalResponse(
                results=[],
                sources=[],
                policy_decision=policy_service.resolve([], question.lower()),
                has_evidence=False,
                out_of_scope=True,
                context_text="",
            )

        # Unique source filenames
        sources_seen: List[str] = []
        for r in filtered:
            if r.chunk.filename not in sources_seen:
                sources_seen.append(r.chunk.filename)

        # Policy resolution
        question_lower = question.lower()
        policy_decision = policy_service.resolve(sources_seen, question_lower)

        # Remove superseded sources from context
        final_results = [
            r for r in filtered
            if r.chunk.filename not in policy_decision.superseded_sources
        ]
        if not final_results:
            # If only superseded docs were found, still show them but with conflict note
            final_results = filtered

        final_sources = list(dict.fromkeys(
            r.chunk.filename for r in final_results
            if r.chunk.filename not in policy_decision.superseded_sources
        ))
        if not final_sources:
            final_sources = sources_seen

        context_text = self._assemble_context(final_results, policy_decision, question)

        return RetrievalResponse(
            results=final_results,
            sources=final_sources,
            policy_decision=policy_decision,
            has_evidence=bool(final_results),
            out_of_scope=False,
            context_text=context_text,
        )

    def _assemble_context(
        self,
        results: List[RetrievalResult],
        policy_decision: PolicyDecision,
        question: str,
    ) -> str:
        parts: List[str] = []

        # Conflict / policy preamble
        preamble = policy_service.build_conflict_preamble(policy_decision)
        if preamble:
            parts.append(f"=== POLICY RESOLUTION NOTES ===\n{preamble}\n")

        parts.append("=== EVIDENCE FROM COURSE DOCUMENTS ===")

        for r in results:
            parts.append(
                f"\n--- Source: {r.chunk.filename} "
                f"(Authority: {r.chunk.authority_score}, "
                f"Section: {r.chunk.section_heading}) ---\n"
                f"{r.chunk.text}"
            )

        return "\n".join(parts)


retrieval_service = RetrievalService()

