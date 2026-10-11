"""Bounded PostgreSQL full-text and exact-identifier retrieval.

The current schema has no stored search vector. This uses a query-time
``to_tsvector('simple', text)`` expression plus parameterized substring
conditions for identifier-like terms. A GIN migration can be considered
later when corpus size and query plans justify it.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Float, and_, bindparam, case, cast, func, literal_column, or_, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.retrieval.base import BackendResult, RetrievalBackendError, SearchCandidate
from app.retrieval.query_service import PreparedQuery

_IDENTIFIER = re.compile(
    r"(?i)\b(?:[a-z0-9]+(?:[-_.][a-z0-9]+)+|(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]{4,})\b"
)


class KeywordSearch:
    """Search authoritative PostgreSQL chunks without depending on Qdrant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(self, query: PreparedQuery) -> BackendResult:
        statement = keyword_statement(query)
        try:
            rows = (await self._session.execute(statement)).all()
        except (OperationalError, DBAPIError, OSError, TimeoutError) as exc:
            raise RetrievalBackendError("keyword", "Keyword search is unavailable") from exc
        except Exception as exc:
            raise RetrievalBackendError("keyword", "Keyword search failed") from exc
        return BackendResult(
            candidates=tuple(_candidate(chunk, document, bid, score) for chunk, document, bid, score in rows)
        )


def keyword_statement(query: PreparedQuery):
    """Build a parameterized FTS query with exact identifier matching."""
    configuration = literal_column("'simple'::regconfig")
    query_parameter = bindparam("search_query", value=query.text)
    vector = func.to_tsvector(configuration, func.coalesce(DocumentChunk.text, ""))
    tsquery = func.websearch_to_tsquery(configuration, query_parameter)
    rank = func.ts_rank_cd(vector, tsquery)

    exact_conditions = _exact_conditions(query.text)
    exact_match = or_(*exact_conditions)
    score = cast(rank, Float) + case((exact_match, 1.0), else_=0.0)
    conditions = [or_(vector.op("@@")(tsquery), exact_match)]
    filters = query.filters
    if filters.bid_id is not None:
        conditions.append(Document.bid_id == filters.bid_id)
    if filters.document_ids:
        conditions.append(Document.id.in_(filters.document_ids))
    if filters.file_types:
        conditions.append(Document.document_type.in_(filters.file_types))
    if filters.chunk_types:
        conditions.append(DocumentChunk.chunk_type.in_(filters.chunk_types))
    if filters.file_name is not None:
        conditions.append(Document.file_name == filters.file_name)

    return (
        select(DocumentChunk, Document, Bid, score.label("keyword_score"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(Bid, Bid.id == Document.bid_id)
        .where(and_(*conditions))
        .order_by(score.desc(), DocumentChunk.chunk_index, DocumentChunk.id)
        .limit(query.keyword_candidates)
    )


def identifier_terms(query: str) -> tuple[str, ...]:
    """Extract exact-looking procurement identifiers without hard-coded values."""
    return tuple(dict.fromkeys(match.group(0).lower() for match in _IDENTIFIER.finditer(query)))


def _exact_conditions(query: str) -> list[Any]:
    lowered = query.lower()
    values = [lowered, *identifier_terms(query)]
    terms = list(dict.fromkeys(item for item in values if item))
    conditions: list[Any] = []
    for term in terms:
        conditions.extend(
            [
                func.lower(DocumentChunk.text).contains(term, autoescape=True),
                func.lower(Bid.bid_identifier).contains(term, autoescape=True),
                func.lower(func.coalesce(Bid.solicitation_number, "")).contains(
                    term,
                    autoescape=True,
                ),
            ]
        )
    return conditions


def _candidate(
    chunk: DocumentChunk,
    document: Document,
    bid: Bid,
    score: object,
) -> SearchCandidate:
    metadata = dict(chunk.chunk_metadata or {})
    metadata.update(
        {
            "file_type": document.document_type,
            "chunk_index": chunk.chunk_index,
            "content_hash": chunk.content_hash,
            "document_date": (
                None if document.document_date is None else document.document_date.isoformat()
            ),
            "addendum_number": document.addendum_number,
            "bid_identifier": bid.bid_identifier,
            "solicitation_number": bid.solicitation_number,
        }
    )
    return SearchCandidate(
        bid_id=document.bid_id,
        document_id=document.id,
        chunk_id=chunk.id,
        file_name=document.file_name,
        text=chunk.text,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        chunk_type=chunk.chunk_type,
        keyword_score=max(0.0, float(score)),
        metadata=metadata,
    )
