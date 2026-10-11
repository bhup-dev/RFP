"""PostgreSQL chunk loading for indexing.

The embedder and Qdrant client are fakes. If rows are committed, cleanup
deletes only a bid whose identifier starts with ``test-index-`` and matches
this run.
"""

import asyncio
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.ingestion.indexing.indexing_service import IndexingService, SqlChunkSource
from tests.test_indexing_pipeline import FakeEmbedder, IndexDouble

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    """The configured PostgreSQL database could not be contacted."""


def test_postgres_chunks_are_indexed_with_stable_point_ids() -> None:
    identifier = f"test-index-{uuid4().hex}"
    try:
        asyncio.run(_run(identifier))
    except DatabaseUnreachable as exc:
        pytest.skip(_safe_message(exc.__cause__ or exc))


async def _run(identifier: str) -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    connected = False
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                connected = True
                await _exercise(session, identifier)
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            if connected:
                await _remove_test_bid(engine, identifier)
        finally:
            await engine.dispose()


async def _exercise(session, identifier: str) -> None:
    bid = Bid(id=uuid4(), bid_identifier=identifier)
    other = Bid(id=uuid4(), bid_identifier=f"{identifier}-other")
    session.add(bid)
    session.add(other)
    await session.flush()
    document = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="pricing.pdf",
        document_type="pdf",
        storage_path=f"{identifier}/pricing.pdf",
        document_date=date(2026, 4, 1),
        addendum_number=2,
        ingestion_status="pending",
    )
    untouched = Document(
        id=uuid4(),
        bid_id=other.id,
        file_name="other.pdf",
        document_type="pdf",
        storage_path=f"{identifier}/other.pdf",
        ingestion_status="pending",
    )
    session.add(document)
    session.add(untouched)
    await session.flush()
    chunk = DocumentChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=4,
        section_title="Pricing",
        chunk_type="table",
        text="PN-100 | 2",
        content_hash="hash-1",
        chunk_metadata={
            "heading_path": ["Pricing"],
            "table_id": "table-1",
            "section_title": "Pricing",
            "anchor": "pricing",
        },
    )
    other_chunk = DocumentChunk(
        id=uuid4(),
        document_id=untouched.id,
        chunk_index=0,
        page_number=1,
        chunk_type="text",
        text="Other bid text",
        content_hash="hash-other",
        chunk_metadata={},
    )
    session.add(chunk)
    session.add(other_chunk)
    await session.commit()

    embedder = FakeEmbedder()
    index = IndexDouble()
    service = IndexingService(
        SqlChunkSource(session),
        embedder,
        index,
        batch_size=2,
        dimension=4,
    )
    first = await service.index_document(document.id)
    second = await service.index_document(document.id)
    chunk.text = "PN-200 | 4"
    chunk.content_hash = "hash-2"
    await session.commit()
    third = await service.index_document(document.id)

    stored = index.stored()
    payload = stored[str(chunk.id)].payload
    assert first.points_upserted == 1
    assert first.ingestion_status == "indexed"
    assert second.points_upserted == 1
    assert len(stored) == 1
    assert third.points_upserted == 1
    assert payload["text"] == "PN-200 | 4"
    assert payload["chunk_id"] == str(chunk.id)
    assert payload["bid_id"] == str(bid.id)
    assert payload["page_number"] == 4
    assert payload["heading_path"] == ["Pricing"]
    assert payload["table_id"] == "table-1"
    assert payload["document_date"] == "2026-04-01"
    assert payload["addendum_number"] == 2
    assert payload["content_hash"] == "hash-2"
    assert embedder.calls[-1] == ["PN-200 | 4"]
    assert str(other_chunk.id) not in stored
    await session.refresh(document)
    await session.refresh(untouched)
    assert document.ingestion_status == "indexed"
    assert untouched.ingestion_status == "pending"
    rows = list(await session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)))
    assert [row.text for row in rows] == ["PN-200 | 4"]


async def _remove_test_bid(engine, identifier: str) -> None:
    if not identifier.startswith("test-index-"):
        return
    identifiers = (identifier, f"{identifier}-other")
    async with engine.connect() as connection:
        transaction = await connection.begin()
        for item in identifiers:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM bids WHERE bid_identifier = :identifier"),
                    {"identifier": item},
                )
            ).scalar_one()
            if count:
                await connection.execute(
                    text("DELETE FROM bids WHERE bid_identifier = :identifier"),
                    {"identifier": item},
                )
        await transaction.commit()


def _safe_message(exc: BaseException) -> str:
    detail = str(exc).lower()
    if "does not exist" in detail:
        reason = "the configured database does not exist"
    elif "password" in detail or "authentication" in detail:
        reason = "authentication failed"
    elif "refused" in detail or "could not connect" in detail:
        reason = "the server refused the connection"
    else:
        reason = type(exc).__name__
    return f"PostgreSQL is not available ({reason})"
