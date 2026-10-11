"""Generated PDF and HTML through PostgreSQL, chunks, points, and retrieval."""

import asyncio
from pathlib import Path
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
from app.services.qdrant_service import IndexedPoint, VectorHit
from tests.pdf_samples import write_text_pdf

pytestmark = pytest.mark.integration


class DatabaseUnreachable(Exception):
    pass


class Embedder:
    def __init__(self) -> None:
        self.calls = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[0.1, 0.2, 0.3, 0.4] for _text in texts]


class Points:
    def __init__(self) -> None:
        self.points = {}
        self.upsert_calls = 0

    def ensure_collection(self):
        return None

    def upsert(self, points):
        self.upsert_calls += 1
        for point in points:
            self.points[point.point_id] = point

    def retrieve(self, point_ids):
        return [
            IndexedPoint(
                point_id=str(value),
                payload=self.points[str(value)].to_payload(),
            )
            for value in point_ids
            if str(value) in self.points
        ]

    def search(self, vector, **kwargs):
        hits = []
        for point in self.points.values():
            payload = point.to_payload()
            if kwargs.get("bid_id") and payload["bid_id"] != str(kwargs["bid_id"]):
                continue
            if kwargs.get("document_ids") and payload["document_id"] not in {
                str(value) for value in kwargs["document_ids"]
            }:
                continue
            if kwargs.get("file_types") and payload.get("file_type") not in kwargs["file_types"]:
                continue
            if kwargs.get("chunk_types") and payload.get("chunk_type") not in kwargs["chunk_types"]:
                continue
            hits.append(
                VectorHit(
                    point_id=point.point_id,
                    score=0.5,
                    payload=payload,
                )
            )
        return hits[: kwargs.get("limit", 10)]


def test_generated_documents_complete_workflow_and_repeat_safely(tmp_path: Path) -> None:
    identifier = f"test-e2e-{uuid4().hex}"
    root = tmp_path / "bids"
    bid_folder = root / identifier
    bid_folder.mkdir(parents=True)
    pdf = bid_folder / "specification.pdf"
    write_text_pdf(
        pdf,
        ["Model WD22TB4 requires a three-year manufacturer warranty and delivery within 30 days."],
    )
    html = bid_folder / "addendum.html"
    html.write_text(
        "<html><body><h1 id='parts'>Addendum Parts</h1>"
        "<table id='parts-table'><tr><th>Part</th><th>Qty</th></tr>"
        "<tr><td>CC7802</td><td>4</td></tr></table></body></html>",
        encoding="utf-8",
    )
    try:
        asyncio.run(_run(root, identifier, pdf))
    except DatabaseUnreachable as exc:
        pytest.skip(f"PostgreSQL is not available ({type(exc.__cause__).__name__})")


async def _run(root: Path, identifier: str, pdf: Path) -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    points = Points()
    embedder = Embedder()
    test_settings = settings.model_copy(
        update={
            "ingestion_input_root": root,
            "embedding_dimension": 4,
            "query_rewriting_enabled": False,
            "reranking_enabled": False,
        }
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                pipeline = EndToEndPipeline(
                    IngestionPipeline(SqlDocumentRegistry(session), root),
                    SqlWorkflowSource(session, root),
                    DocumentParseStage(root),
                    ChunkingPipeline.from_settings(
                        SqlChunkRepository(session),
                        test_settings,
                    ),
                    IndexingService(
                        SqlChunkSource(session),
                        embedder,
                        points,
                        batch_size=8,
                        dimension=4,
                    ),
                )
                first = await pipeline.process_directory(identifier)
                assert first.files_discovered == 2
                assert first.documents_completed == 2
                assert first.documents_failed == 0
                assert first.chunks_created >= 2
                assert first.points_upserted == len(points.points)
                document_ids = {item.document_id for item in first.results}
                assert len(document_ids) == 2
                assert all(item.bid_id == first.results[0].bid_id for item in first.results)
                bid_id = first.results[0].bid_id
                assert bid_id is not None

                chunk_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(Document.bid_id == first.results[0].bid_id)
                    )
                )
                calls_before = len(embedder.calls)
                upserts_before = points.upsert_calls
                second = await pipeline.process_directory(identifier)
                assert second.documents_completed == 2
                assert second.documents_skipped == 2
                assert len(embedder.calls) == calls_before
                assert points.upsert_calls == upserts_before
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(Document.bid_id == bid_id)
                    )
                ) == chunk_count
                retrieval = RetrievalService(
                    SemanticSearch(embedder, points, 4),
                    KeywordSearch(session),
                    citation_validator=CitationResolver(session),
                )
                model = await retrieval.search(
                    RetrievalRequest(
                        query="WD22TB4",
                        bid_id=bid_id,
                        top_k=3,
                    )
                )
                assert model.results
                assert "WD22TB4" in model.results[0].text
                assert model.results[0].bid_id == bid_id
                assert model.results[0].document_id in document_ids
                assert model.results[0].page_number == 1
                assert model.results[0].semantic_score is not None
                assert model.results[0].keyword_score is not None

                table = await retrieval.search(
                    RetrievalRequest(
                        query="CC7802",
                        bid_id=bid_id,
                        chunk_types=["table"],
                        top_k=3,
                    )
                )
                assert table.results[0].chunk_type == "table"
                assert table.results[0].metadata["table_id"]
                assert table.results[0].metadata["anchor"] == "parts-table"

                audit = await ConsistencyAuditor(session, points).audit(bid_id=bid_id)
                assert audit.chunks_checked == chunk_count
                assert audit.points_found == chunk_count
                assert audit.failures == []

                pdf.write_bytes(pdf.read_bytes() + b"\n")
                changed = await pipeline.process_directory(identifier)
                changed_result = next(
                    item for item in changed.results if item.relative_path.endswith("specification.pdf")
                )
                assert changed_result.registration_outcome == "changed"
                assert changed_result.completed is False
                assert int(
                    await session.scalar(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(Document.bid_id == bid_id)
                    )
                ) == chunk_count
        except (OSError, OperationalError, InterfaceError, DBAPIError, TimeoutError) as exc:
            raise DatabaseUnreachable from exc
    finally:
        try:
            async with engine.begin() as connection:
                bid_id = await connection.scalar(
                    select(Bid.id).where(Bid.bid_identifier == identifier)
                )
                if bid_id is not None:
                    await connection.execute(delete(Bid).where(Bid.id == bid_id))
        finally:
            await engine.dispose()
