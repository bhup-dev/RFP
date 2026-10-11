"""Interactions across configuration, persistence, vectors, and the API.

External services are mocked. These tests do not start PostgreSQL, Qdrant,
or a model provider.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.db.session as db_session
from app.config import settings
from app.core.logging import HANDLER_NAME
from app.db.models import Bid, Document, DocumentChunk
from app.main import app
from app.schemas.bid import BidRead
from app.schemas.chunk import ChunkRead
from app.schemas.citation import Citation
from app.schemas.document import DocumentRead
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES, BidExtraction
from app.schemas.retrieval import RetrievedChunk
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.provider_errors import ProviderRequestError
from app.services.qdrant_service import (
    ChunkPoint,
    CollectionDimensionError,
    QdrantReadiness,
    QdrantService,
)

ASSIGNMENT_NAMES = (
    "Bid Number",
    "Title",
    "Due Date",
    "Bid Submission Type",
    "Term of Bid",
    "Pre Bid Meeting",
    "Installation",
    "Bid Bond Requirement",
    "Delivery Date",
    "Payment Terms",
    "Additional Documentation Required",
    "MFG for Registration",
    "Contract or Cooperative to use",
    "Model_no",
    "Part_no",
    "Product",
    "contact_info",
    "company_name",
    "Bid Summary",
    "Product Specification",
)


def test_services_share_one_settings_object() -> None:
    import app.core.health as health
    import app.core.logging as logging_config
    import app.db.session as session
    import app.main as main
    import app.services.embedding_service as embeddings
    import app.services.llm_service as llm
    import app.services.qdrant_service as qdrant

    assert (
        session.settings
        is qdrant.settings
        is llm.settings
        is embeddings.settings
        is logging_config.settings
        is health.settings
        is main.settings
        is settings
    )
    assert EmbeddingService()._settings.embedding_dimension == settings.embedding_dimension
    assert QdrantService()._settings.embedding_dimension == settings.embedding_dimension
    assert LLMService()._settings.llm_model == settings.llm_model
    assert EmbeddingService()._settings.embedding_model == settings.embedding_model
    assert EmbeddingService()._client is None
    assert QdrantService()._client is None
    assert LLMService()._client is None


def test_embedding_and_qdrant_reject_a_dimension_mismatch() -> None:
    shared = settings.model_copy(
        update={
            "embedding_dimension": 4,
            "embedding_model": "text-embedding-3-small",
            "qdrant_collection_name": "cross_module_chunks",
            "openai_api_key": "sk-cross-test",
        }
    )

    class Embeddings:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])]
            )

    embeddings = Embeddings()
    embedding_service = EmbeddingService(
        client=SimpleNamespace(embeddings=embeddings),
        settings_obj=shared,
    )
    with pytest.raises(ProviderRequestError, match="EMBEDDING_DIMENSION is 4"):
        asyncio.run(embedding_service.embed_text("Payment terms are net 30."))
    assert embeddings.calls[0]["dimensions"] == 4

    class Vectors:
        def __init__(self) -> None:
            self.upserts = 0
            self.deleted = False

        def collection_exists(self, _name: str) -> bool:
            return True

        def get_collection(self, _name: str) -> object:
            vectors = SimpleNamespace(size=8)
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

        def upsert(self, **_kwargs: object) -> None:
            self.upserts += 1

        def delete_collection(self, _name: str) -> None:
            self.deleted = True

    client = Vectors()
    qdrant = QdrantService(client=client, settings_obj=shared)
    point = ChunkPoint(
        bid_id=uuid4(),
        document_id=uuid4(),
        chunk_id=uuid4(),
        vector=(0.1, 0.2, 0.3, 0.4),
        file_name="addendum_1.pdf",
        text="Payment terms are net 30.",
    )
    with pytest.raises(CollectionDimensionError, match="left unchanged"):
        qdrant.upsert([point])
    assert client.upserts == 0
    assert client.deleted is False


def test_models_schemas_and_qdrant_payload_share_identifiers() -> None:
    now = datetime.now(UTC)
    bid = Bid(
        id=uuid4(),
        bid_identifier="Bid1",
        solicitation_number="SOL-9",
        title="Office supplies",
        issuing_organization="City Hall",
        created_at=now,
        updated_at=now,
    )
    document = Document(
        id=uuid4(),
        bid_id=bid.id,
        file_name="addendum_1.pdf",
        document_type="addendum",
        storage_path="/tmp/addendum_1.pdf",
        ingestion_status="pending",
        created_at=now,
        updated_at=now,
    )
    chunk = DocumentChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=2,
        section_title="Payment Terms",
        chunk_type="text",
        text="Payment terms are net 30.",
        chunk_metadata={"source": "parser"},
        created_at=now,
    )

    bid_read = BidRead.model_validate(bid)
    document_read = DocumentRead.model_validate(document)
    chunk_read = ChunkRead.model_validate(chunk)
    assert document_read.bid_id == bid_read.id
    assert chunk_read.document_id == document_read.id
    assert chunk_read.metadata == {"source": "parser"}
    assert "storage_path" not in document_read.model_dump()

    citation = Citation(
        bid_id=bid.id,
        document_id=document.id,
        chunk_id=chunk.id,
        file_name=document.file_name,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        excerpt=chunk.text,
    )
    point = ChunkPoint(
        bid_id=citation.bid_id,
        document_id=citation.document_id,
        chunk_id=citation.chunk_id,
        vector=(0.1, 0.2, 0.3, 0.4),
        file_name=citation.file_name,
        text=citation.excerpt,
        page_number=citation.page_number,
        section_title=citation.section_title,
        chunk_type=chunk.chunk_type,
    )
    payload = point.to_payload()
    assert point.point_id == str(chunk.id) == str(citation.chunk_id)
    assert payload["bid_id"] == str(citation.bid_id) == str(document.bid_id)
    assert payload["document_id"] == str(citation.document_id)
    assert payload["text"] == citation.excerpt

    retrieved = RetrievedChunk(
        bid_id=citation.bid_id,
        document_id=citation.document_id,
        chunk_id=citation.chunk_id,
        file_name=citation.file_name,
        text=payload["text"],
        page_number=citation.page_number,
        section_title=citation.section_title,
        chunk_type=chunk.chunk_type,
        semantic_score=0.9,
    )
    assert retrieved.chunk_id == chunk_read.id

    extraction = BidExtraction()
    serialized = extraction.model_dump(by_alias=True)
    assert tuple(serialized) == ASSIGNMENT_NAMES
    assert ASSIGNMENT_FIELD_NAMES == ASSIGNMENT_NAMES
    assert all(value is None for value in serialized.values())


def test_liveness_stays_up_when_readiness_dependencies_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[object] = []

    def fail_openai(*_args: object, **_kwargs: object) -> None:
        constructed.append(object())
        raise AssertionError("OpenAI client was constructed")

    monkeypatch.setattr("app.services.llm_service.AsyncOpenAI", fail_openai)
    monkeypatch.setattr("app.services.embedding_service.AsyncOpenAI", fail_openai)

    async def postgres_down() -> dict[str, str]:
        return {"status": "unavailable", "detail": "PostgreSQL is unavailable"}

    async def qdrant_down() -> dict[str, str]:
        return {"status": "unavailable", "detail": "Qdrant is unavailable"}

    monkeypatch.setattr("app.core.health.postgresql_check", postgres_down)
    monkeypatch.setattr("app.core.health.qdrant_check", qdrant_down)

    with TestClient(app) as client:
        live = client.get("/health")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 503
    body = ready.json()
    assert body["postgresql"]["status"] == "unavailable"
    assert body["qdrant"]["status"] == "unavailable"
    assert "Traceback" not in ready.text
    assert constructed == []
    assert db_session._engine is None


def test_qdrant_health_returns_before_a_hung_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "health_check_timeout_seconds", 0.05)

    class Slow:
        def readiness(self) -> QdrantReadiness:
            time.sleep(0.4)
            return QdrantReadiness(connected=True, collection_ready=True)

    monkeypatch.setattr("app.main.get_qdrant_service", lambda: Slow())
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get("/health/qdrant")
        elapsed = time.perf_counter() - started
    assert response.status_code == 503
    assert response.json()["detail"] == "Qdrant readiness check timed out"
    assert elapsed < 0.3


def test_repeated_lifecycles_keep_one_log_handler() -> None:
    with TestClient(app):
        pass
    with TestClient(app):
        pass
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "name", None) == HANDLER_NAME
    ]
    assert len(handlers) == 1
    assert db_session._engine is None
