"""PostgreSQL persistence and cross-bid citation isolation for extraction."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.extraction import ExtractedField, ExtractionRun
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES, ExtractionRunStatus
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.extraction_service import ExtractionService, SqlExtractionStore

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
    def __init__(self, chunk_id):
        self.chunk_id = chunk_id

    async def generate_response(self, _prompt, **_kwargs):
        fields = {
            name: {
                "value": None,
                "confidence": None,
                "chunk_ids": [],
                "notes": None,
                "status": "not_found",
            }
            for name in ASSIGNMENT_FIELD_NAMES
        }
        fields["Bid Number"] = {
            "value": "SOL-EXTRACT-1",
            "confidence": 0.9,
            "chunk_ids": [str(self.chunk_id)],
            "notes": "Stated in the solicitation header.",
            "status": "extracted",
        }
        return json.dumps(fields)


def test_extraction_runs_are_historical_and_citations_are_bid_scoped() -> None:
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    own_bid_id = uuid4()
    foreign_bid_id = uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                own_document = Document(
                    id=uuid4(),
                    bid_id=own_bid_id,
                    file_name="solicitation.pdf",
                    document_type="pdf",
                    storage_path="test/solicitation.pdf",
                    ingestion_status="indexed",
                )
                foreign_document = Document(
                    id=uuid4(),
                    bid_id=foreign_bid_id,
                    file_name="foreign.pdf",
                    document_type="pdf",
                    storage_path="test/foreign.pdf",
                    ingestion_status="indexed",
                )
                own_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=own_document.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Header",
                    chunk_type="text",
                    text="Solicitation number SOL-EXTRACT-1.",
                    content_hash="a" * 64,
                    chunk_metadata={},
                )
                foreign_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=foreign_document.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Header",
                    chunk_type="text",
                    text="Unrelated solicitation SOL-FOREIGN.",
                    content_hash="b" * 64,
                    chunk_metadata={},
                )
                session.add_all(
                    [
                        Bid(id=own_bid_id, bid_identifier=f"test-extract-{uuid4().hex}"),
                        Bid(id=foreign_bid_id, bid_identifier=f"test-extract-{uuid4().hex}"),
                        own_document,
                        foreign_document,
                        own_chunk,
                        foreign_chunk,
                    ]
                )
                await session.commit()

                retrieved = RetrievedChunk(
                    bid_id=own_bid_id,
                    document_id=own_document.id,
                    chunk_id=own_chunk.id,
                    file_name=own_document.file_name,
                    text=own_chunk.text,
                    page_number=1,
                    section_title="Header",
                    chunk_type="text",
                    keyword_score=1.0,
                )
                valid = ExtractionService(
                    SqlExtractionStore(session),
                    Retriever(retrieved),
                    LLM(own_chunk.id),
                    model_name="mock-model",
                )
                first = await valid.extract(own_bid_id)
                assert first.status == ExtractionRunStatus.COMPLETED
                assert first.fields.bid_number is not None
                assert first.fields.bid_number.citations[0].chunk_id == own_chunk.id

                cross_bid = ExtractionService(
                    SqlExtractionStore(session),
                    Retriever(retrieved),
                    LLM(foreign_chunk.id),
                    model_name="mock-model",
                )
                second = await cross_bid.extract(own_bid_id)
                assert second.status == ExtractionRunStatus.PARTIAL
                assert second.fields.bid_number is not None
                assert second.fields.bid_number.value is None
                assert second.fields.bid_number.citations == []

                runs = (
                    await session.scalars(
                        select(ExtractionRun)
                        .where(ExtractionRun.bid_id == own_bid_id)
                        .order_by(ExtractionRun.created_at)
                    )
                ).all()
                assert len(runs) == 2
                assert runs[0].id != runs[1].id
                assert runs[0].document_ids == [str(own_document.id)]
                stored = (
                    await session.scalars(
                        select(ExtractedField).where(
                            ExtractedField.extraction_run_id == runs[0].id,
                            ExtractedField.field_name == "Bid Number",
                        )
                    )
                ).one()
                assert stored.value == "SOL-EXTRACT-1"
                assert stored.validation_status == "supported"
                assert stored.citations[0]["chunk_id"] == str(own_chunk.id)
                assert stored.citations[0]["document_id"] == str(own_document.id)
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Bid).where(Bid.id.in_([own_bid_id, foreign_bid_id]))
                )
        finally:
            await engine.dispose()
