"""Opt-in live workflow across PostgreSQL, OpenAI embeddings, and Qdrant."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models.bid import Bid
from app.ingestion.chunking.pipeline import ChunkingPipeline, SqlChunkRepository
from app.ingestion.consistency import ConsistencyAuditor
from app.ingestion.end_to_end_pipeline import EndToEndPipeline, SqlWorkflowSource
from app.ingestion.indexing.indexing_service import IndexingService, SqlChunkSource
from app.ingestion.parse_stage import DocumentParseStage
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import SqlDocumentRegistry
from app.retrieval.citations import CitationResolver
from app.retrieval.keyword_search import KeywordSearch
from app.retrieval.retrieval_service import RetrievalService
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.retrieval import RetrievalRequest
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService
from tests.pdf_samples import write_text_pdf

pytestmark = [pytest.mark.integration, pytest.mark.qdrant, pytest.mark.live_e2e]


def test_live_generated_pdf_to_citation_ready_search(tmp_path: Path) -> None:
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("set RUN_LIVE_E2E=1 to call the configured embedding provider")
    asyncio.run(_run(tmp_path))


async def _run(tmp_path: Path) -> None:
    identifier = f"test-live-e2e-{uuid4().hex}"
    collection = f"rfp_live_e2e_{uuid4().hex[:12]}"
    root = tmp_path / "bids"
    folder = root / identifier
    folder.mkdir(parents=True)
    write_text_pdf(
        folder / "requirements.pdf",
        ["BPM044557 requires delivery within 30 days and a three-year warranty."],
    )
    live_settings = settings.model_copy(
        update={
            "ingestion_input_root": root,
            "qdrant_collection_name": collection,
            "query_rewriting_enabled": False,
            "reranking_enabled": False,
        }
    )
    assert live_settings.openai_api_key
    engine = create_async_engine(live_settings.database_url, poolclass=NullPool)
    client = QdrantClient(
        url=live_settings.qdrant_url,
        api_key=live_settings.qdrant_api_key,
        timeout=10,
        prefer_grpc=False,
        check_compatibility=False,
    )
    vectors = QdrantService(client=client, settings_obj=live_settings)
    embedder = EmbeddingService(settings_obj=live_settings)
    created = False
    try:
        client.get_collections()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            pipeline = EndToEndPipeline(
                IngestionPipeline(SqlDocumentRegistry(session), root),
                SqlWorkflowSource(session, root),
                DocumentParseStage(root),
                ChunkingPipeline.from_settings(
                    SqlChunkRepository(session),
                    live_settings,
                ),
                IndexingService(
                    SqlChunkSource(session),
                    embedder,
                    vectors,
                    batch_size=8,
                    dimension=live_settings.embedding_dimension,
                ),
            )
            processed = await pipeline.process_directory(identifier)
            created = client.collection_exists(collection)
            assert processed.documents_completed == 1
            assert processed.documents_failed == 0
            bid_id = processed.results[0].bid_id
            assert bid_id is not None

            retrieval = RetrievalService(
                SemanticSearch(
                    embedder,
                    vectors,
                    live_settings.embedding_dimension,
                ),
                KeywordSearch(session),
                citation_validator=CitationResolver(session),
            )
            started = time.perf_counter()
            response = await retrieval.search(
                RetrievalRequest(
                    query="BPM044557 delivery warranty",
                    bid_id=bid_id,
                    top_k=3,
                )
            )
            search_latency_ms = (time.perf_counter() - started) * 1000
            assert response.retrieval_mode.value == "hybrid"
            assert response.results
            result = response.results[0]
            assert "BPM044557" in result.text
            assert result.bid_id == bid_id
            assert result.document_id == processed.results[0].document_id
            assert result.page_number == 1
            assert result.semantic_score is not None
            assert result.keyword_score is not None
            assert result.reranker_score is None

            audit = await ConsistencyAuditor(session, vectors).audit(bid_id=bid_id)
            assert audit.chunks_checked >= 1
            assert audit.chunks_checked == audit.points_found
            assert audit.failures == []
            print(
                json.dumps(
                    {
                        "documents_processed": processed.documents_completed,
                        "chunks_indexed": audit.chunks_checked,
                        "indexing_failures": processed.documents_failed,
                        "citation_failures": len(audit.failures),
                        "search_latency_ms": round(search_latency_ms, 3),
                        "retrieval_mode": response.retrieval_mode.value,
                        "top_k": 3,
                        "rewriting": False,
                        "reranking": False,
                    },
                    sort_keys=True,
                )
            )
    finally:
        await embedder.close()
        if created:
            client.delete_collection(collection)
        client.close()
        try:
            async with engine.begin() as connection:
                bid_id = await connection.scalar(
                    select(Bid.id).where(Bid.bid_identifier == identifier)
                )
                if bid_id is not None:
                    await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
