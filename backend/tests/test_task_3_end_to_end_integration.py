"""Synthetic PostgreSQL workflow from persisted chunks through final profile."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.addendum import AddendumAnalysisRun
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.extraction import ExtractionRun
from app.db.models.validation import ValidationRun
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.addendum_analysis_service import AddendumAnalysisService, SqlAddendumStore
from app.services.bid_intelligence_service import BidIntelligenceService, SqlIntelligenceStore
from app.services.bid_intelligence_workflow import BidIntelligenceWorkflow
from app.services.extraction_service import ExtractionService, SqlExtractionStore
from app.services.extraction_validation_service import (
    ExtractionValidationService,
    SqlValidationStore,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


class Retriever:
    def __init__(self, results):
        self.results = results

    async def search(self, request):
        allowed = set(request.document_ids)
        rows = [
            item
            for item in self.results
            if item.bid_id == request.bid_id
            and (not allowed or item.document_id in allowed)
        ]
        return RetrievalResponse(
            query=request.query,
            bid_id=request.bid_id,
            results=rows[: request.top_k],
            total_results=min(len(rows), request.top_k),
            retrieval_mode="hybrid",
        )


class ExtractionLLM:
    def __init__(self, original_chunk_id):
        self.original_chunk_id = original_chunk_id

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
        fields["Due Date"] = {
            "value": "May 1 at 2 PM ET",
            "confidence": 0.9,
            "chunk_ids": [str(self.original_chunk_id)],
            "notes": "Original solicitation deadline.",
            "status": "extracted",
        }
        return json.dumps(fields)


class AddendumLLM:
    def __init__(self, original_chunk_id, addendum_chunk_id):
        self.original_chunk_id = original_chunk_id
        self.addendum_chunk_id = addendum_chunk_id
        self.calls = 0

    async def generate_response(self, _prompt, **_kwargs):
        self.calls += 1
        if self.calls % 2:
            return json.dumps(
                {
                    "topics": [
                        {
                            "topic": "Due deadline",
                            "retrieval_query": "proposal due deadline",
                            "addendum_chunk_ids": [str(self.addendum_chunk_id)],
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "findings": [
                    {
                        "topic": "Due deadline",
                        "classification": "conflict",
                        "original_value": "May 1 at 2 PM ET",
                        "amended_value": "May 8 at 4 PM ET",
                        "explanation": "The addendum states a different deadline without explicit supersession.",
                        "impact_note": "Human review is required.",
                        "severity": "high",
                        "confidence": 0.9,
                        "original_chunk_ids": [str(self.original_chunk_id)],
                        "addendum_chunk_ids": [str(self.addendum_chunk_id)],
                    }
                ]
            }
        )


class ValidationLLM:
    async def generate_response(self, *_args, **_kwargs):
        raise AssertionError("deterministic conflict validation should not call the LLM")


def test_complete_task_3_workflow_preserves_lineage_conflicts_and_history():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bid_id = uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                original = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="base.pdf",
                    document_type="pdf",
                    storage_path="unseen-package/base.pdf",
                    ingestion_status="indexed",
                )
                addendum = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="change.pdf",
                    document_type="pdf",
                    storage_path="unseen-package/change.pdf",
                    ingestion_status="indexed",
                )
                original_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=original.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Submission",
                    chunk_type="text",
                    text="Request for Proposals. Proposals are due May 1 at 2 PM ET.",
                    content_hash="f" * 64,
                    chunk_metadata={},
                )
                addendum_chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=addendum.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Deadline",
                    chunk_type="text",
                    text=(
                        "Addendum Number 1 to the solicitation. "
                        "Proposals are due May 8 at 4 PM ET."
                    ),
                    content_hash="1" * 64,
                    chunk_metadata={},
                )
                session.add_all(
                    [
                        Bid(
                            id=bid_id,
                            bid_identifier=f"unseen-package-{uuid4().hex}",
                            solicitation_number="SYN-90817",
                        ),
                        original,
                        addendum,
                        original_chunk,
                        addendum_chunk,
                    ]
                )
                await session.commit()
                retrieved = [
                    RetrievedChunk(
                        bid_id=bid_id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        file_name=document.file_name,
                        text=chunk.text,
                        page_number=1,
                        section_title=chunk.section_title,
                        chunk_type="text",
                        keyword_score=1.0,
                    )
                    for document, chunk in (
                        (original, original_chunk),
                        (addendum, addendum_chunk),
                    )
                ]
                retriever = Retriever(retrieved)
                workflow = BidIntelligenceWorkflow(
                    ExtractionService(
                        SqlExtractionStore(session),
                        retriever,
                        ExtractionLLM(original_chunk.id),
                        model_name="mock-extraction",
                    ),
                    AddendumAnalysisService(
                        SqlAddendumStore(session),
                        retriever,
                        AddendumLLM(original_chunk.id, addendum_chunk.id),
                        model_name="mock-addendum",
                    ),
                    ExtractionValidationService(
                        SqlValidationStore(session),
                        ValidationLLM(),
                        model_name="mock-validation",
                        semantic_enabled=False,
                    ),
                    BidIntelligenceService(SqlIntelligenceStore(session)),
                )

                first = await workflow.execute(bid_id)
                second = await workflow.execute(bid_id)
                assert first.status.value == second.status.value == "completed"
                assert first.profile is not None
                due = first.profile.fields["Due Date"]
                assert due.value == "May 1 at 2 PM ET"
                assert due.validation_status.value == "conflicting"
                assert due.supporting_citations[0].chunk_id == original_chunk.id
                assert {item.chunk_id for item in due.contradicting_citations} == {
                    original_chunk.id,
                    addendum_chunk.id,
                }
                assert due.addendum_changes[0].original_value == "May 1 at 2 PM ET"
                assert due.addendum_changes[0].amended_value == "May 8 at 4 PM ET"
                assert first.profile.metadata.status.value == "needs_review"
                assert first.profile.metadata.review_required is True
                assert tuple(first.profile.fields) == ASSIGNMENT_FIELD_NAMES
                assert first.profile.metadata.extraction_run_id == first.stages[0].run_id
                assert first.profile.metadata.addendum_analysis_run_id == first.stages[1].run_id
                assert first.profile.metadata.validation_run_id == first.stages[2].run_id

                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ExtractionRun)
                        .where(ExtractionRun.bid_id == bid_id)
                    )
                ) == 2
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AddendumAnalysisRun)
                        .where(AddendumAnalysisRun.bid_id == bid_id)
                    )
                ) == 2
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ValidationRun)
                        .where(ValidationRun.bid_id == bid_id)
                    )
                ) == 2
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
