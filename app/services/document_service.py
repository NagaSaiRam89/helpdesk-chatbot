"""
app/services/document_service.py
Document ingestion, chunking, fingerprinting and index management.
Reads all .txt files from course_docs/ at startup; rebuilds when fingerprint changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import get_settings
from app.services.policy_service import NON_AUTHORITATIVE_MARKERS, DOCUMENT_AUTHORITY

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class DocumentChunk:
    chunk_id: str
    filename: str
    section_heading: str
    text: str
    authority_score: int        # from DOCUMENT_AUTHORITY
    is_authoritative: bool      # False for student forum comments etc.
    char_start: int
    char_end: int


@dataclass
class DocumentMeta:
    filename: str
    raw_text: str
    fingerprint: str            # SHA-256 of raw text
    authority_score: int
    last_updated: Optional[str]
    status: Optional[str]
    chunks: List[DocumentChunk] = field(default_factory=list)


class DocumentService:
    """
    Loads and indexes course documents.
    Call load() once at startup; it is safe to call again (uses cached state).
    """

    def __init__(self):
        self._docs: Dict[str, DocumentMeta] = {}
        self._all_chunks: List[DocumentChunk] = []
        self._corpus_fingerprint: str = ""
        self._loaded: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self, docs_dir: Optional[str] = None) -> None:
        """Read and index all .txt files from course_docs/."""
        path = Path(docs_dir or settings.COURSE_DOCS_DIR)
        if not path.exists():
            raise FileNotFoundError(f"course_docs directory not found: {path}")

        txt_files = sorted(path.glob("*.txt"))
        if not txt_files:
            raise ValueError(f"No .txt files found in {path}")

        new_docs: Dict[str, DocumentMeta] = {}
        for fp in txt_files:
            meta = self._ingest_file(fp)
            new_docs[meta.filename] = meta
            logger.info("Loaded %s (%d chunks, fingerprint=%s)",
                        meta.filename, len(meta.chunks), meta.fingerprint[:8])

        self._docs = new_docs
        self._all_chunks = [c for m in self._docs.values() for c in m.chunks]
        self._corpus_fingerprint = self._compute_corpus_fingerprint()
        self._loaded = True
        logger.info("Document index ready: %d documents, %d chunks, corpus=%s",
                    len(self._docs), len(self._all_chunks), self._corpus_fingerprint[:12])

    def get_all_chunks(self) -> List[DocumentChunk]:
        self._ensure_loaded()
        return self._all_chunks

    def get_corpus_fingerprint(self) -> str:
        self._ensure_loaded()
        return self._corpus_fingerprint

    def get_document_names(self) -> List[str]:
        self._ensure_loaded()
        return list(self._docs.keys())

    def get_document_count(self) -> int:
        return len(self._docs)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _ingest_file(self, filepath: Path) -> DocumentMeta:
        raw_text = filepath.read_text(encoding="utf-8", errors="replace")
        fingerprint = hashlib.sha256(raw_text.encode()).hexdigest()
        filename = filepath.name
        authority_score = DOCUMENT_AUTHORITY.get(filename, 50)

        # Extract metadata from first few lines
        last_updated = self._extract_field(raw_text, "LAST UPDATED")
        status = self._extract_field(raw_text, "STATUS")

        chunks = self._chunk_document(filename, raw_text, authority_score)

        return DocumentMeta(
            filename=filename,
            raw_text=raw_text,
            fingerprint=fingerprint,
            authority_score=authority_score,
            last_updated=last_updated,
            status=status,
            chunks=chunks,
        )

    def _extract_field(self, text: str, field_name: str) -> Optional[str]:
        pattern = rf"^{re.escape(field_name)}\s*:\s*(.+)$"
        m = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _chunk_document(
        self, filename: str, raw_text: str, authority_score: int
    ) -> List[DocumentChunk]:
        """
        Split document into chunks at section boundaries.
        Each chunk carries its heading and authoritative flag.
        Special handling for doc 04 to flag student forum section as non-authoritative.
        """
        chunks: List[DocumentChunk] = []
        # Split at numbered section headers like "1. TITLE" or "2. TITLE"
        section_pattern = re.compile(
            r"(?=^\d+\.\s+[A-Z][A-Z\s&]+)", re.MULTILINE
        )
        parts = section_pattern.split(raw_text)

        # First part is document header metadata (before first numbered section)
        header = parts[0].strip()
        if header:
            chunk_id = f"{filename}::header"
            is_auth = not self._is_non_authoritative(header)
            chunks.append(DocumentChunk(
                chunk_id=chunk_id,
                filename=filename,
                section_heading="DOCUMENT HEADER",
                text=header,
                authority_score=authority_score if is_auth else 0,
                is_authoritative=is_auth,
                char_start=0,
                char_end=len(header),
            ))

        char_offset = len(parts[0])
        for i, section_text in enumerate(parts[1:], 1):
            # Extract section heading from first line
            first_line_match = re.match(r"(\d+\.\s+[^\n]+)", section_text)
            heading = first_line_match.group(1).strip() if first_line_match else f"Section {i}"

            is_auth = not self._is_non_authoritative(section_text)
            chunk_id = f"{filename}::section_{i}"

            # For large sections, further split into sub-chunks of ~400 chars
            sub_chunks = self._sub_chunk(section_text, 500)
            for j, sub in enumerate(sub_chunks):
                sc_id = f"{chunk_id}_{j}" if len(sub_chunks) > 1 else chunk_id
                chunks.append(DocumentChunk(
                    chunk_id=sc_id,
                    filename=filename,
                    section_heading=heading,
                    text=sub.strip(),
                    authority_score=authority_score if is_auth else 0,
                    is_authoritative=is_auth,
                    char_start=char_offset,
                    char_end=char_offset + len(sub),
                ))
                char_offset += len(sub)

        return [c for c in chunks if c.text]

    def _sub_chunk(self, text: str, max_chars: int) -> List[str]:
        """Split text into chunks trying to break at paragraph or sentence boundaries."""
        if len(text) <= max_chars:
            return [text]
        chunks = []
        paragraphs = re.split(r"\n\s*\n", text)
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_chars:
                current = current + "\n\n" + para if current else para
            else:
                if current:
                    chunks.append(current)
                # If single paragraph is still too long, force split
                if len(para) > max_chars:
                    for k in range(0, len(para), max_chars):
                        chunks.append(para[k:k + max_chars])
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        return chunks or [text]

    def _is_non_authoritative(self, text: str) -> bool:
        text_lower = text.lower()
        return any(m in text_lower for m in NON_AUTHORITATIVE_MARKERS)

    def _compute_corpus_fingerprint(self) -> str:
        combined = "|".join(
            f"{name}:{meta.fingerprint}"
            for name, meta in sorted(self._docs.items())
        )
        return hashlib.sha256(combined.encode()).hexdigest()

    def _ensure_loaded(self):
        if not self._loaded:
            raise RuntimeError("DocumentService.load() has not been called.")


# Singleton instance
document_service = DocumentService()

