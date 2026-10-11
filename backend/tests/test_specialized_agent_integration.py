import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.state import initial_state
from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.specialized_agent import SpecializedAgentFinding
from app.schemas.citation import Citation
from app.schemas.specialized_agents import (
    SpecializedAgentResult,
    SpecializedFinding,
)
from app.services.orchestration_store import SqlOrchestrationStore
from app.services.specialized_agent_store import SqlSpecializedAgentStore

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


def test_specialized_findings_are_durable_and_citations_are_bid_scoped():
    try:
        asyncio.run(_run())
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bid_id, other_bid_id = uuid4(), uuid4()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                document = Document(
                    id=uuid4(),
                    bid_id=bid_id,
                    file_name="arbitrary-package.pdf",
                    document_type="pdf",
                    storage_path="arbitrary/package.pdf",
                    ingestion_status="indexed",
                )
                chunk = DocumentChunk(
                    id=uuid4(),
                    document_id=document.id,
                    chunk_index=0,
                    page_number=2,
                    section_title="Products",
                    chunk_type="text",
                    text="Provide 12 Router X units, model RX-100/A.",
                    content_hash="a" * 64,
                    chunk_metadata={},
                )
                session.add_all(
                    [
                        Bid(id=bid_id, bid_identifier=f"agent-{bid_id}"),
                        Bid(id=other_bid_id, bid_identifier=f"agent-{other_bid_id}"),
                        document,
                        chunk,
                    ]
                )
                await session.commit()
                workflow_state = initial_state(
                    uuid4(), bid_id, "1.0", datetime.now(UTC)
                )
                await SqlOrchestrationStore(session).create_run(workflow_state)
                citation = Citation(
                    bid_id=bid_id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    file_name=document.file_name,
                    page_number=2,
                    section_title="Products",
                    excerpt=chunk.text,
                )
                now = datetime.now(UTC)
                result = SpecializedAgentResult(
                    run_id=uuid4(),
                    workflow_id=workflow_state["workflow_id"],
                    bid_id=bid_id,
                    agent_name="product_specification",
                    status="completed",
                    findings=[
                        SpecializedFinding(
                            finding_id="product_specification:router-x",
                            title="Router X",
                            description="Twelve Router X units are requested.",
                            category="product",
                            classification="mandatory",
                            attributes={
                                "product_name": "Router X",
                                "model_number": "RX-100/A",
                                "quantity": 12,
                                "unit": "units",
                            },
                            citations=[citation],
                            confidence=0.95,
                        )
                    ],
                    evidence_chunk_count=1,
                    model_name="mock",
                    started_at=now,
                    completed_at=now,
                )
                store = SqlSpecializedAgentStore(session)
                await store.save_result(result)
                restored = await store.load_result(
                    workflow_state["workflow_id"], result.agent_name
                )
                assert restored is not None
                assert restored.findings[0].attributes["model_number"] == "RX-100/A"
                assert restored.findings[0].citations[0].chunk_id == chunk.id
                assert int(
                    await session.scalar(
                        select(func.count()).select_from(SpecializedAgentFinding)
                    )
                ) == 1

                valid = await store.validate_citations(bid_id, [citation])
                assert valid[0].valid is True
                cross_bid = citation.model_copy(update={"bid_id": other_bid_id})
                invalid = await store.validate_citations(bid_id, [cross_bid])
                assert invalid[0].valid is False
                assert invalid[0].reason == "citation belongs to another bid"
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(Bid).where(Bid.id.in_([bid_id, other_bid_id]))
                )
        finally:
            await engine.dispose()
