"""PostgreSQL chunk persistence.

The test rolls back its transaction. If a row is left behind, only a bid whose
identifier starts with ``test-chunk-`` and matches this run is deleted.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.ingestion.chunking.base import ChunkDocument
from app.ingestion.chunking.pipeline import ChunkingPipeline, SqlChunkRepository
from app.schemas.parsing import ParsedDocument, ParsedSection

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    """The configured PostgreSQL database could not be contacted."""


class _Rollback(Exception):
    """Abort the test transaction after the assertions."""


def test_postgres_chunk_replacement_rolls_back() -> None:
    identifier = f"test-chunk-{uuid4().hex}"
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
                async with session.begin():
                    await _exercise(session, identifier)
                    raise _Rollback()
        except _Rollback:
            pass
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            if connected:
                await _remove_if_committed(engine, identifier)
        finally:
            await engine.dispose()


async def _exercise(session: AsyncSession, identifier: str) -> None:
    bid = Bid(id=uuid4(), bid_identifier=identifier)
    session.add(bid)
    await session.flush()
    first = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="notes.pdf",
        document_type="pdf",
        storage_path=f"{identifier}/notes.pdf",
        ingestion_status="pending",
    )
    second = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="other.pdf",
        document_type="pdf",
        storage_path=f"{identifier}/other.pdf",
        ingestion_status="pending",
    )
    session.add(first)
    session.add(second)
    await session.flush()
    pipeline = ChunkingPipeline.from_settings(SqlChunkRepository(session), settings)

    created = await pipeline.persist(_source(first, bid.id), _text(first.id, "Original specification text"))
    repeated = await pipeline.persist(_source(first, bid.id), _text(first.id, "Original specification text"))
    await pipeline.persist(_source(second, bid.id), _text(second.id, "Other document text"))
    replaced = await pipeline.persist(_source(first, bid.id), _text(first.id, "Revised specification text"))

    rows = list(
        await session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == first.id).order_by(DocumentChunk.chunk_index)
        )
    )
    other_rows = list(
        await session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == second.id))
    )
    assert created.chunks_created == 1
    assert repeated.chunks_skipped == 1
    assert replaced.chunks_created == 1
    assert [row.text for row in rows] == ["Revised specification text"]
    assert rows[0].content_hash
    assert rows[0].chunk_metadata["bid_id"] == str(bid.id)
    assert rows[0].chunk_metadata["addendum_number"] is None
    assert [row.text for row in other_rows] == ["Other document text"]
    await session.refresh(first)
    assert first.ingestion_status == "pending"


def _source(document: Document, bid_id) -> ChunkDocument:
    return ChunkDocument(
        document_id=document.id,
        bid_id=bid_id,
        file_name=document.file_name,
        file_type=document.document_type,
        relative_path=document.storage_path,
    )


def _text(document_id, text: str) -> ParsedDocument:
    return ParsedDocument(
        document_id=document_id,
        relative_path="notes.pdf",
        file_name="notes.pdf",
        document_type="pdf",
        parser_name="test",
        parser_version="0",
        status="parsed",
        text=text,
        sections=[ParsedSection(page_number=1, text=text)],
        tables=[],
        warnings=[],
        errors=[],
        metadata={},
    )


async def _remove_if_committed(engine, identifier: str) -> None:
    if not identifier.startswith("test-chunk-"):
        return
    async with engine.connect() as connection:
        transaction = await connection.begin()
        count = (
            await connection.execute(
                text("SELECT count(*) FROM bids WHERE bid_identifier = :identifier"),
                {"identifier": identifier},
            )
        ).scalar_one()
        if count:
            await connection.execute(
                text("DELETE FROM bids WHERE bid_identifier = :identifier"),
                {"identifier": identifier},
            )
            await transaction.commit()
            raise AssertionError("chunking test rows were committed and then removed")
        await transaction.rollback()


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
