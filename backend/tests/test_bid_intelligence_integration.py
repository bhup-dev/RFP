import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.extraction import ExtractedField, ExtractionRun
from app.db.models.validation import FieldValidationRecord, ValidationRun
from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.services.bid_intelligence_service import (
    BidIntelligenceService,
    SqlIntelligenceStore,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_profile_reads_consistent_runs_without_mutating_upstream_history():
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
                document = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="source.pdf",
                    document_type="pdf",
                    storage_path="test/source.pdf",
                    ingestion_status="indexed",
                )
                chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=document.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Header",
                    chunk_type="text",
                    text="Solicitation number SOL-PROFILE-1.",
                    content_hash="e" * 64,
                    chunk_metadata={},
                )
                extraction = ExtractionRun(
                    id=uuid4(),
                    bid_id=bid_id,
                    status="completed",
                    model_name="mock",
                    document_ids=[str(document.id)],
                )
                validation = ValidationRun(
                    id=uuid4(),
                    bid_id=bid_id,
                    extraction_run_id=extraction.id,
                    status="completed",
                    overall_status="needs_review",
                    model_name="mock",
                    thresholds={"high": 0.8, "medium": 0.55},
                    addendum_check_status="not_run",
                )
                citation = Citation(
                    bid_id=bid_id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    file_name=document.file_name,
                    page_number=1,
                    section_title="Header",
                    excerpt=chunk.text,
                ).model_dump(mode="json")
                extracted = [
                    ExtractedField(
                        extraction_run_id=extraction.id,
                        field_name=name,
                        value="SOL-PROFILE-1" if name == "Bid Number" else None,
                        confidence=Decimal("0.95") if name == "Bid Number" else None,
                        notes="Header." if name == "Bid Number" else None,
                        validation_status="supported" if name == "Bid Number" else "missing",
                        citations=[citation] if name == "Bid Number" else [],
                    )
                    for name in ASSIGNMENT_FIELD_NAMES
                ]
                validated = [
                    FieldValidationRecord(
                        validation_run_id=validation.id,
                        field_name=name,
                        extracted_value="SOL-PROFILE-1" if name == "Bid Number" else None,
                        status="validated" if name == "Bid Number" else "not_found",
                        confidence_score=Decimal("1.0") if name == "Bid Number" else None,
                        confidence_category="high" if name == "Bid Number" else "unavailable",
                        supporting_citations=[citation] if name == "Bid Number" else [],
                        contradicting_citations=[],
                        reasons=["Checked."],
                        deterministic_checks={},
                        recommended_action="Routine use." if name == "Bid Number" else "Inspect if required.",
                    )
                    for name in ASSIGNMENT_FIELD_NAMES
                ]
                session.add_all(
                    [
                        Bid(
                            id=bid_id,
                            bid_identifier=f"test-profile-{uuid4().hex}",
                            solicitation_number="SOL-PROFILE-1",
                        ),
                        document,
                        chunk,
                        extraction,
                        validation,
                        *extracted,
                        *validated,
                    ]
                )
                await session.commit()
                counts_before = (
                    int(await session.scalar(select(func.count()).select_from(ExtractionRun))),
                    int(await session.scalar(select(func.count()).select_from(ValidationRun))),
                )

                service = BidIntelligenceService(SqlIntelligenceStore(session))
                first = await service.build(bid_id)
                second = await service.build(bid_id)
                assert first.metadata.profile_id == second.metadata.profile_id
                assert first.fields == second.fields
                assert first.fields["Bid Number"].value == "SOL-PROFILE-1"
                assert first.fields["Bid Number"].validation_status.value == "validated"
                assert first.metadata.extraction_run_id == extraction.id
                assert first.metadata.validation_run_id == validation.id
                assert counts_before == (
                    int(await session.scalar(select(func.count()).select_from(ExtractionRun))),
                    int(await session.scalar(select(func.count()).select_from(ValidationRun))),
                )
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
