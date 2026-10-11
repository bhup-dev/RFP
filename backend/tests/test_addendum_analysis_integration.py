"""PostgreSQL addendum-run persistence and cross-bid citation isolation."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.addendum_analysis_service import (
    AddendumAnalysisService,
    SqlAddendumStore,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


class Retriever:
    def __init__(self, result):
        self.result = result

    async def search(self, request):
        return RetrievalResponse(
            query=request.query,
            bid_id=request.bid_id,
            results=[self.result],
            total_results=1,
            retrieval_mode="hybrid",
        )


class LLM:
    def __init__(self, base_chunk_id, addendum_chunk_id):
        self.base_chunk_id = base_chunk_id
        self.addendum_chunk_id = addendum_chunk_id
        self.calls = 0

    async def generate_response(self, _prompt, **_kwargs):
        self.calls += 1
        if self.calls % 2:
            return json.dumps(
                {
                    "topics": [
                        {
                            "topic": "Submission deadline",
                            "retrieval_query": "submission deadline",
                            "addendum_chunk_ids": [str(self.addendum_chunk_id)],
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "findings": [
                    {
                        "topic": "Submission deadline",
                        "classification": "modified_requirement",
                        "original_value": "May 1 at 2 PM ET",
                        "amended_value": "May 8 at 4 PM ET",
                        "explanation": "The addendum explicitly revises the deadline.",
                        "impact_note": "Use the revised deadline.",
                        "severity": "high",
                        "confidence": 0.98,
                        "original_chunk_ids": [str(self.base_chunk_id)],
                        "addendum_chunk_ids": [str(self.addendum_chunk_id)],
                    }
                ]
            }
        )


def test_addendum_analysis_persists_history_and_authoritative_sources() -> None:
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bid_id = uuid4()
    foreign_bid_id = uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                original = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="solicitation.pdf",
                    document_type="pdf",
                    storage_path="test/solicitation.pdf",
                    ingestion_status="indexed",
                )
                addendum = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="notice.pdf",
                    document_type="pdf",
                    addendum_number=None,
                    storage_path="test/notice.pdf",
                    ingestion_status="indexed",
                )
                foreign_document = Document(
                    id=uuid4(),
                    bid_id=foreign_bid_id,
                    file_name="foreign.pdf",
                    document_type="solicitation",
                    storage_path="test/foreign.pdf",
                    ingestion_status="indexed",
                )
                original_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=original.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Deadline",
                    chunk_type="text",
                    text="Request for Proposals. Proposals are due May 1 at 2 PM ET.",
                    content_hash="a" * 64,
                    chunk_metadata={},
                )
                addendum_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=addendum.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Deadline Revision",
                    chunk_type="text",
                    text=(
                        "Addendum Number 1 to the solicitation. "
                        "The proposal deadline is revised to May 8 at 4 PM ET."
                    ),
                    content_hash="b" * 64,
                    chunk_metadata={},
                )
                foreign_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=foreign_document.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Deadline",
                    chunk_type="text",
                    text="Foreign bid deadline.",
                    content_hash="c" * 64,
                    chunk_metadata={},
                )
                session.add_all(
                    [
                        Bid(id=bid_id, bid_identifier=f"test-addendum-{uuid4().hex}"),
                        Bid(
                            id=foreign_bid_id,
                            bid_identifier=f"test-addendum-{uuid4().hex}",
                        ),
                        original,
                        addendum,
                        foreign_document,
                        original_chunk,
                        addendum_chunk,
                        foreign_chunk,
                    ]
                )
                await session.commit()

                retrieved = RetrievedChunk(
                    bid_id=bid_id,
                    document_id=original.id,
                    chunk_id=original_chunk.id,
                    file_name=original.file_name,
                    text=original_chunk.text,
                    page_number=1,
                    section_title="Deadline",
                    chunk_type="text",
                    keyword_score=1.0,
                )
                store = SqlAddendumStore(session)
                service = AddendumAnalysisService(
                    store,
                    Retriever(retrieved),
                    LLM(original_chunk.id, addendum_chunk.id),
                    model_name="mock-model",
                )
                first = await service.analyze(bid_id)
                assert first.status.value == "completed"
                assert first.relationship_uncertain is False
                assert first.ordering_uncertain is False
                assert first.addendum_documents[0].document_id == addendum.id
                assert first.findings[0].original_sources[0].chunk_id == original_chunk.id
                assert first.findings[0].addendum_sources[0].chunk_id == addendum_chunk.id

                invalid = AddendumAnalysisService(
                    store,
                    Retriever(retrieved),
                    LLM(foreign_chunk.id, addendum_chunk.id),
                    model_name="mock-model",
                )
                second = await invalid.analyze(bid_id)
                assert second.status.value == "partial"
                assert second.findings == []

                runs = (
                    await session.scalars(
                        select(AddendumAnalysisRun)
                        .where(AddendumAnalysisRun.bid_id == bid_id)
                        .order_by(AddendumAnalysisRun.created_at)
                    )
                ).all()
                assert len(runs) == 2
                assert runs[0].id != runs[1].id
                assert runs[0].original_document_ids == [str(original.id)]
                assert runs[0].addendum_document_ids == [str(addendum.id)]
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AddendumChange)
                        .where(AddendumChange.bid_id == bid_id)
                    )
                ) == 1
                stored = await session.scalar(
                    select(AddendumChange).where(
                        AddendumChange.analysis_run_id == runs[0].id
                    )
                )
                assert stored is not None
                assert stored.status == "modified_requirement"
                assert stored.original_document_id == original.id
                assert stored.addendum_document_id == addendum.id
                assert {item["side"] for item in stored.sources} == {
                    "original",
                    "addendum",
                }
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Bid).where(Bid.id.in_([bid_id, foreign_bid_id]))
                )
        finally:
            await engine.dispose()
