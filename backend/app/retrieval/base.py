"""Shared retrieval contracts used by semantic, keyword, and fusion modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SearchCandidate:
    """One citation-ready chunk from one or both retrieval backends."""

    bid_id: UUID
    document_id: UUID
    chunk_id: UUID
    file_name: str
    text: str
    page_number: int | None = None
    section_title: str | None = None
    chunk_type: str | None = None
    semantic_score: float | None = None
    keyword_score: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class BackendResult:
    """Candidates and safe warnings from one successful backend."""

    candidates: tuple[SearchCandidate, ...]
    warnings: tuple[str, ...] = ()


class RetrievalBackendError(RuntimeError):
    """A backend failed. Its public message contains no query or credentials."""

    def __init__(self, backend: str, message: str) -> None:
        self.backend = backend
        self.public_message = message
        super().__init__(message)


class RetrievalUnavailable(RuntimeError):
    """Both retrieval backends failed or fallback is disabled."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(errors))
