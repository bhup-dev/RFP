"""Live PostgreSQL keyword retrieval with exact identifiers and bid filters."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.query_service import prepare_query
from app.schemas.retrieval import RetrievalRequest

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


class _Rollback(Exception):
    pass


def test_keyword_search_finds_exact_identifiers_and_enforces_bid_scope() -> None:
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    await _exercise(session)
                    raise _Rollback()
        except _Rollback:
            pass
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        await engine.dispose()


async def _exercise(session) -> None:
    bid = Bid(id=uuid4(), bid_identifier=f"test-search-{uuid4().hex}")
    other_bid = Bid(id=uuid4(), bid_identifier=f"test-search-{uuid4().hex}")
    session.add_all([bid, other_bid])
    await session.flush()
    document = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="products.pdf",
        document_type="pdf",
        storage_path="test/products.pdf",
        ingestion_status="indexed",
    )
    other_document = Document(
        id=uuid4(),
        bid_id=other_bid.id,
        file_name="other.pdf",
        document_type="pdf",
        storage_path="test/other.pdf",
        ingestion_status="indexed",
    )
    session.add_all([document, other_document])
    await session.flush()
    exact = DocumentChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=3,
        section_title="Products",
        chunk_type="table",
        text=(
            "Solicitation | Model | Part | Reference | Qty\n"
            "BPM044557 | WD22TB4 | JA-207652 | 168884 | 4\n"
            "E20P4600040 | CC7802 | JA-207652 | 168884 | 2"
        ),
        content_hash="exact",
        chunk_metadata={"heading_path": ["Products"], "table_id": "table-1"},
    )
    natural = DocumentChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=1,
        page_number=4,
        section_title="Delivery",
        chunk_type="text",
        text="Delivery is required within thirty days after award.",
        content_hash="natural",
        chunk_metadata={},
    )
    outsider = DocumentChunk(
        id=uuid4(),
        document_id=other_document.id,
        chunk_index=0,
        page_number=1,
        chunk_type="table",
        text="WD22TB4 is listed for another bid.",
        content_hash="other",
        chunk_metadata={},
    )
    session.add_all([exact, natural, outsider])
    await session.flush()
    search = KeywordSearch(session)

    for query in (
        "BPM044557",
        "E20P4600040",
        "JA-207652",
        "168884",
        "CC7802",
        "WD22TB4",
    ):
        result = await search.search(
            prepare_query(
                RetrievalRequest(
                    query=query,
                    bid_id=bid.id,
                    file_types=["pdf"],
                    chunk_types=["table"],
                )
            )
        )
        assert [candidate.chunk_id for candidate in result.candidates] == [exact.id]
        assert result.candidates[0].metadata["table_id"] == "table-1"
        assert result.candidates[0].page_number == 3

    natural_result = await search.search(
        prepare_query(
            RetrievalRequest(
                query='"delivery" thirty days',
                document_ids=[document.id],
            )
        )
    )
    assert [candidate.chunk_id for candidate in natural_result.candidates] == [natural.id]
