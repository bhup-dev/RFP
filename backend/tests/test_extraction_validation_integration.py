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
from app.services.extraction_validation_service import (
    ExtractionValidationService,
    SqlValidationStore,
)

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


class LLM:
    async def generate_response(self, *_args, **_kwargs):
        raise AssertionError("deterministic validation must not call the provider")


def test_validation_history_persists_without_mutating_extraction():
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
                    file_name="solicitation.pdf",
                    document_type="pdf",
                    storage_path="test/solicitation.pdf",
                    ingestion_status="indexed",
                )
                chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=document.id,
                    chunk_index=0,
                    page_number=1,
                    section_title="Header",
                    chunk_type="text",
                    text="Solicitation number SOL-VALIDATE-1.",
                    content_hash="d" * 64,
                    chunk_metadata={},
                )
                extraction = ExtractionRun(
                    id=uuid4(),
                    bid_id=bid_id,
                    status="completed",
                    model_name="mock-model",
                    document_ids=[str(document.id)],
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
                fields = [
                    ExtractedField(
                        extraction_run_id=extraction.id,
                        field_name=name,
                        value="SOL-VALIDATE-1" if name == "Bid Number" else None,
                        confidence=Decimal("0.99000") if name == "Bid Number" else None,
                        validation_status="supported" if name == "Bid Number" else "missing",
                        citations=[citation] if name == "Bid Number" else [],
                    )
                    for name in ASSIGNMENT_FIELD_NAMES
                ]
                session.add_all(
                    [
                        Bid(id=bid_id, bid_identifier=f"test-validate-{uuid4().hex}"),
                        document,
                        chunk,
                        extraction,
                        *fields,
                    ]
                )
                await session.commit()

                service = ExtractionValidationService(
                    SqlValidationStore(session),
                    LLM(),
                    model_name="mock-model",
                    semantic_enabled=False,
                )
                first = await service.validate(bid_id, extraction.id)
                second = await service.validate(bid_id, extraction.id)
                assert first.validation_run_id != second.validation_run_id
                assert first.fields["Bid Number"].validation_status.value == "validated"
                assert first.fields["Bid Number"].confidence_score == 1.0

                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ValidationRun)
                        .where(ValidationRun.extraction_run_id == extraction.id)
                    )
                ) == 2
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(FieldValidationRecord)
                        .join(ValidationRun)
                        .where(ValidationRun.extraction_run_id == extraction.id)
                    )
                ) == 40
                original = await session.scalar(
                    select(ExtractedField).where(
                        ExtractedField.extraction_run_id == extraction.id,
                        ExtractedField.field_name == "Bid Number",
                    )
                )
                assert original is not None
                assert original.value == "SOL-VALIDATE-1"
                assert str(original.confidence) == "0.99000"
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
