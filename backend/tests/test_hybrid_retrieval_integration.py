"""Optional PostgreSQL + Qdrant hybrid retrieval with a fake query embedder."""

import asyncio
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.retrieval import RetrievalRequest
from app.services.qdrant_service import ChunkPoint, QdrantService

pytestmark = [pytest.mark.integration, pytest.mark.qdrant]


class _Rollback(Exception):
    pass


class _Embedder:
    async def embed_texts(self, texts):
        assert texts == ["WD22TB4"]
        return [[1.0, 0.0, 0.0, 0.0]]


def test_bid_scoped_hybrid_search_preserves_table_citation() -> None:
    collection = f"retrieval_test_{uuid4().hex[:12]}"
    test_settings = settings.model_copy(
        update={"qdrant_collection_name": collection, "embedding_dimension": 4}
    )
    client = QdrantClient(
        url=test_settings.qdrant_url,
        api_key=test_settings.qdrant_api_key,
        timeout=2,
        prefer_grpc=False,
        check_compatibility=False,
    )
    try:
        client.get_collections()
    except Exception as exc:
        client.close()
        pytest.skip(f"Qdrant is not available ({type(exc).__name__})")

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    qdrant = QdrantService(client=client, settings_obj=test_settings)
    created = False
    try:
        qdrant.ensure_collection()
        created = True
        try:
            asyncio.run(_exercise(engine, qdrant))
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            pytest.skip(f"PostgreSQL is not available ({type(exc).__name__})")
    finally:
        if created:
            client.delete_collection(collection)
        client.close()
        asyncio.run(engine.dispose())


async def _exercise(engine, qdrant: QdrantService) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                bid = Bid(id=uuid4(), bid_identifier=f"test-hybrid-{uuid4().hex}")
                outsider_bid = Bid(
                    id=uuid4(),
                    bid_identifier=f"test-hybrid-{uuid4().hex}",
                )
                session.add_all([bid, outsider_bid])
                await session.flush()
                document = Document(
                    id=uuid4(),
                    bid_id=bid.id,
                    file_name="products.pdf",
                    document_type="pdf",
                    storage_path="test/products.pdf",
                    ingestion_status="indexed",
                )
                outsider_document = Document(
                    id=uuid4(),
                    bid_id=outsider_bid.id,
                    file_name="other.pdf",
                    document_type="pdf",
                    storage_path="test/other.pdf",
                    ingestion_status="indexed",
                )
                session.add_all([document, outsider_document])
                await session.flush()
                target = DocumentChunk(
                    id=uuid4(),
                    document_id=document.id,
                    chunk_index=0,
                    page_number=3,
                    section_title="Products",
                    chunk_type="table",
                    text="Model | Quantity\nWD22TB4 | 2",
                    content_hash="target",
                    chunk_metadata={
                        "heading_path": ["Products"],
                        "table_id": "table-1",
                    },
                )
                outsider = DocumentChunk(
                    id=uuid4(),
                    document_id=outsider_document.id,
                    chunk_index=0,
                    page_number=1,
                    chunk_type="table",
                    text="WD22TB4 belongs to another bid",
                    content_hash="outsider",
                    chunk_metadata={},
                )
                session.add_all([target, outsider])
                await session.flush()
                qdrant.upsert(
                    [
                        _point(target, document, bid.id),
                        _point(outsider, outsider_document, outsider_bid.id),
                    ]
                )
                service = RetrievalService(
                    SemanticSearch(_Embedder(), qdrant, 4),
                    KeywordSearch(session),
                )
                response = await service.search(
                    RetrievalRequest(
                        query="WD22TB4",
                        bid_id=bid.id,
                        file_types=["pdf"],
                        chunk_types=["table"],
                        top_k=5,
                    )
                )
                assert response.retrieval_mode.value == "hybrid"
                assert [item.chunk_id for item in response.results] == [target.id]
                result = response.results[0]
                assert result.page_number == 3
                assert result.chunk_type == "table"
                assert result.metadata["table_id"] == "table-1"
                assert result.semantic_score is not None
                assert result.keyword_score is not None
                raise _Rollback()
    except _Rollback:
        pass


def _point(chunk: DocumentChunk, document: Document, bid_id) -> ChunkPoint:
    return ChunkPoint(
        bid_id=bid_id,
        document_id=document.id,
        chunk_id=chunk.id,
        vector=(1.0, 0.0, 0.0, 0.0),
        file_name=document.file_name,
        text=chunk.text,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        chunk_type=chunk.chunk_type,
        metadata={
            "file_type": document.document_type,
            "heading_path": chunk.chunk_metadata.get("heading_path", []),
            "chunk_index": chunk.chunk_index,
            "table_id": chunk.chunk_metadata.get("table_id"),
            "content_hash": chunk.content_hash,
        },
    )
