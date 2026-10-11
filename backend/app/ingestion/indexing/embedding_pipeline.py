"""Prepare persisted chunk text and request embeddings in batches.

The provider is the existing embedding service. This module does not store
vectors and does not call Qdrant.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.schemas.indexing import IndexMessage
from app.services.provider_errors import ProviderConfigurationError, ProviderRequestError


class TextEmbedder(Protocol):
    """Ordered embeddings for a batch of non-empty strings."""

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input, in the same order."""


@dataclass(frozen=True)
class IndexChunk:
    """One PostgreSQL chunk plus the document fields copied into a payload."""

    chunk_id: UUID
    document_id: UUID
    bid_id: UUID
    chunk_index: int
    text: str
    content_hash: str | None
    page_number: int | None
    section_title: str | None
    chunk_type: str | None
    file_name: str
    file_type: str | None
    document_date: date | None
    addendum_number: int | None
    heading_path: tuple[str, ...]
    table_id: str | None
    anchor: str | None


@dataclass(frozen=True)
class EmbeddedChunk:
    """A chunk whose vector passed validation."""

    chunk: IndexChunk
    vector: tuple[float, ...]


@dataclass(frozen=True)
class EmbedBatchResult:
    """Valid vectors, plus one error per chunk that was not embedded."""

    embedded: tuple[EmbeddedChunk, ...]
    errors: tuple[IndexMessage, ...]


class EmbeddingPipeline:
    """Embed chunk text. Invalid vectors are reported and not altered."""

    def __init__(self, embedder: TextEmbedder, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be a positive integer")
        self._embedder = embedder
        self._dimension = dimension

    async def embed(self, chunks: Sequence[IndexChunk]) -> EmbedBatchResult:
        """Embed one batch. A provider failure applies to every chunk in it."""
        items = list(chunks)
        if not items:
            return EmbedBatchResult(embedded=(), errors=())
        try:
            vectors = await self._embedder.embed_texts([chunk.text for chunk in items])
        except ProviderConfigurationError as exc:
            return EmbedBatchResult(
                embedded=(),
                errors=tuple(_failure(chunk, "embedding_configuration", str(exc)) for chunk in items),
            )
        except (ProviderRequestError, ValueError, TypeError, TimeoutError, OSError) as exc:
            return EmbedBatchResult(
                embedded=(),
                errors=tuple(_failure(chunk, "embedding_failed", _safe_provider_message(exc)) for chunk in items),
            )

        if len(vectors) != len(items):
            return EmbedBatchResult(
                embedded=(),
                errors=tuple(
                    _failure(chunk, "embedding_failed", "Embedding provider returned an unexpected number of vectors")
                    for chunk in items
                ),
            )

        embedded: list[EmbeddedChunk] = []
        errors: list[IndexMessage] = []
        for chunk, vector in zip(items, vectors, strict=True):
            problem = vector_problem(vector, self._dimension)
            if problem is not None:
                errors.append(_failure(chunk, "invalid_vector", problem))
                continue
            embedded.append(EmbeddedChunk(chunk=chunk, vector=tuple(float(value) for value in vector)))
        return EmbedBatchResult(embedded=tuple(embedded), errors=tuple(errors))


def vector_problem(vector: object, dimension: int) -> str | None:
    """Reject a vector that cannot be stored. The vector is not resized."""
    if isinstance(vector, str) or not isinstance(vector, Sequence) or not vector:
        return "Embedding provider returned an empty vector"
    if len(vector) != dimension:
        return (
            f"Embedding provider returned dimension {len(vector)}, "
            f"but EMBEDDING_DIMENSION is {dimension}"
        )
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "Embedding provider returned a non-numeric vector"
        if not math.isfinite(float(value)):
            return "Embedding provider returned a non-finite vector"
    return None


def _failure(chunk: IndexChunk, code: str, message: str) -> IndexMessage:
    return IndexMessage(
        code=code,
        message=message,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
    )


def _safe_provider_message(exc: Exception) -> str:
    if isinstance(exc, ProviderRequestError | ValueError):
        text = str(exc).strip()
        if text and "sk-" not in text:
            return text
    return "Embedding request failed"
