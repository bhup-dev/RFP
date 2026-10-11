"""Audit PostgreSQL chunk identity against the Qdrant vector index."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.schemas.workflow import ConsistencyFailure, ConsistencyReport
from app.services.qdrant_service import IndexedPoint


class PointReader(Protocol):
    def retrieve(
        self,
        point_ids: Sequence[UUID | str],
    ) -> list[IndexedPoint]:
        """Return indexed payloads for known ids."""


class ConsistencyAuditor:
    """Report missing or mismatched points; never repair or delete data."""

    def __init__(
        self,
        session: AsyncSession,
        points: PointReader,
        *,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._session = session
        self._points = points
        self._batch_size = batch_size

    async def audit(
        self,
        *,
        bid_id: UUID | None = None,
        document_id: UUID | None = None,
    ) -> ConsistencyReport:
        statement = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        )
        if bid_id is not None:
            statement = statement.where(Document.bid_id == bid_id)
        if document_id is not None:
            statement = statement.where(Document.id == document_id)
        rows = (await self._session.execute(statement)).all()
        failures: list[ConsistencyFailure] = []
        found = 0
        for offset in range(0, len(rows), self._batch_size):
            batch = rows[offset : offset + self._batch_size]
            indexed = await asyncio.to_thread(
                self._points.retrieve,
                [chunk.id for chunk, _document in batch],
            )
            by_id = {point.point_id: point for point in indexed}
            found += len(indexed)
            for chunk, document in batch:
                point = by_id.get(str(chunk.id))
                if point is None:
                    failures.append(
                        _failure(chunk.id, "missing_point", "Qdrant point is missing")
                    )
                    continue
                problem = _point_problem(point, chunk, document)
                if problem is not None:
                    failures.append(problem)
        return ConsistencyReport(
            chunks_checked=len(rows),
            points_found=found,
            failures=failures,
        )


def _point_problem(
    point: IndexedPoint,
    chunk: DocumentChunk,
    document: Document,
) -> ConsistencyFailure | None:
    payload = point.payload
    expected = {
        "chunk_id": str(chunk.id),
        "document_id": str(document.id),
        "bid_id": str(document.bid_id),
        "content_hash": chunk.content_hash,
        "file_name": document.file_name,
        "chunk_type": chunk.chunk_type,
        "page_number": chunk.page_number,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            return _failure(
                chunk.id,
                "payload_mismatch",
                f"Qdrant payload field {key} does not match PostgreSQL",
            )
    if payload.get("text") != chunk.text:
        return _failure(
            chunk.id,
            "payload_mismatch",
            "Qdrant payload text does not match PostgreSQL",
        )
    return None


def _failure(chunk_id: UUID, code: str, message: str) -> ConsistencyFailure:
    return ConsistencyFailure(chunk_id=chunk_id, code=code, message=message)
