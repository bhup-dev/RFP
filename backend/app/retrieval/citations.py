"""Resolve retrieval citations against authoritative PostgreSQL chunks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.schemas.retrieval import RetrievedChunk
from app.schemas.workflow import (
    CitationFailure,
    CitationValidationResult,
)


class CitationResolver:
    """Validate a result list with one database query and no filesystem paths."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate(
        self,
        results: list[RetrievedChunk],
    ) -> CitationValidationResult:
        if not results:
            return CitationValidationResult(checked=0, resolved=0)
        ids = list(dict.fromkeys(result.chunk_id for result in results))
        rows = (
            await self._session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_(ids))
            )
        ).all()
        stored = {chunk.id: (chunk, document) for chunk, document in rows}
        failures: list[CitationFailure] = []
        resolved = 0
        for result in results:
            row = stored.get(result.chunk_id)
            if row is None:
                failures.append(
                    CitationFailure(
                        chunk_id=result.chunk_id,
                        reason="chunk does not exist",
                    )
                )
                continue
            chunk, document = row
            problem = _mismatch(result, chunk, document)
            if problem:
                failures.append(
                    CitationFailure(
                        chunk_id=result.chunk_id,
                        reason=problem,
                    )
                )
            else:
                resolved += 1
        return CitationValidationResult(
            checked=len(results),
            resolved=resolved,
            failures=failures,
        )


def _mismatch(
    result: RetrievedChunk,
    chunk: DocumentChunk,
    document: Document,
) -> str | None:
    expected_title = (chunk.chunk_metadata or {}).get(
        "section_title",
        chunk.section_title,
    )
    checks = (
        (result.document_id == document.id, "document id does not match chunk"),
        (result.bid_id == document.bid_id, "bid id does not match document"),
        (result.file_name == document.file_name, "file name does not match document"),
        (result.text == chunk.text, "citation text does not match chunk"),
        (result.page_number == chunk.page_number, "page number does not match chunk"),
        (result.section_title == expected_title, "section title does not match chunk"),
        (result.chunk_type == chunk.chunk_type, "chunk type does not match chunk"),
    )
    return next((message for valid, message in checks if not valid), None)
