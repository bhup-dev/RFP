"""Indexing pipeline. Embedding and Qdrant calls are mocked."""

import asyncio
import json
import math
from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from qdrant_client.http.exceptions import UnexpectedResponse
from httpx import Headers

from app.config import Settings
from app.ingestion.indexing.embedding_pipeline import EmbeddingPipeline, IndexChunk, vector_problem
from app.ingestion.indexing.indexing_service import IndexingService
from app.ingestion.indexing.qdrant_indexer import chunk_point
from app.services.provider_errors import ProviderRequestError
from app.services.qdrant_service import CollectionDimensionError, QdrantService
from tests.test_qdrant_service import RecordingClient

BID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_BID = UUID("22222222-2222-2222-2222-222222222222")


def _settings(dimension: int = 4) -> Settings:
    return Settings(
        _env_file=None,
        embedding_dimension=dimension,
        qdrant_collection_name="unit_chunks",
        qdrant_api_key=None,
        openai_api_key=None,
    )


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_on: int | None = None
        self.vectors: list[list[float]] | None = None
        self._calls = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        call = self._calls
        self._calls += 1
        if self.fail_on is not None and call == self.fail_on:
            raise ProviderRequestError("Embedding request was rate limited")
        if self.vectors is not None:
            return self.vectors
        return [[float(index + 1), 0.0, 0.0, 0.0] for index, _text in enumerate(texts)]


class MemorySource:
    def __init__(self) -> None:
        self.documents: dict[UUID, SimpleNamespace] = {}
        self.bids: dict[UUID, SimpleNamespace] = {}
        self.chunks: dict[UUID, list[IndexChunk]] = {}
        self.status: dict[UUID, str] = {}
        self.history: list[tuple[UUID, str]] = []

    def add_bid(self, bid_id: UUID) -> None:
        self.bids[bid_id] = SimpleNamespace(id=bid_id)

    def add_document(self, document_id: UUID, bid_id: UUID, chunks: list[IndexChunk]) -> None:
        self.documents[document_id] = SimpleNamespace(id=document_id, bid_id=bid_id)
        self.chunks[document_id] = list(chunks)
        self.status[document_id] = "pending"

    async def get_document(self, document_id: UUID):
        return self.documents.get(document_id)

    async def get_bid(self, bid_id: UUID):
        return self.bids.get(bid_id)

    async def document_ids_for_bid(self, bid_id: UUID) -> list[UUID]:
        return sorted(
            (document_id for document_id, document in self.documents.items() if document.bid_id == bid_id),
            key=str,
        )

    async def load_batch(self, document_id: UUID, *, offset: int, limit: int) -> list[IndexChunk]:
        return self.chunks.get(document_id, [])[offset : offset + limit]

    async def set_status(self, document_id: UUID, status: str) -> None:
        self.status[document_id] = status
        self.history.append((document_id, status))


class IndexDouble:
    def __init__(self, *, vector_size: int = 4, fail_upsert: bool = False) -> None:
        self.client = RecordingClient(exists=True, vector_size=vector_size)
        self.service = QdrantService(client=self.client, settings_obj=_settings(4))
        self.fail_upsert = fail_upsert

    def ensure_collection(self) -> None:
        self.service.ensure_collection()

    def upsert(self, points) -> None:
        if self.fail_upsert:
            raise UnexpectedResponse(500, "unavailable", b"down", Headers())
        self.service.upsert(points)

    def stored(self) -> dict[str, object]:
        points: dict[str, object] = {}
        for call in self.client.calls:
            if call[0] != "upsert":
                continue
            for point in call[2]:
                points[str(point.id)] = point
        return points


def _chunk(
    document_id: UUID,
    bid_id: UUID = BID,
    *,
    text: str = "Valve specification PN-100",
    index: int = 0,
    chunk_id: UUID | None = None,
) -> IndexChunk:
    return IndexChunk(
        chunk_id=chunk_id or uuid4(),
        document_id=document_id,
        bid_id=bid_id,
        chunk_index=index,
        text=text,
        content_hash="abc123",
        page_number=3,
        section_title="Pricing",
        chunk_type="table",
        file_name="pricing.pdf",
        file_type="pdf",
        document_date=date(2026, 4, 1),
        addendum_number=2,
        heading_path=("Pricing", "Schedule"),
        table_id="table-1",
        anchor=None,
    )


def _service(
    source: MemorySource,
    embedder: FakeEmbedder | None = None,
    index: IndexDouble | None = None,
    batch_size: int = 2,
) -> tuple[IndexingService, FakeEmbedder, IndexDouble]:
    vectors = embedder or FakeEmbedder()
    store = index or IndexDouble()
    service = IndexingService(source, vectors, store, batch_size=batch_size, dimension=4)
    return service, vectors, store


def test_batch_embedding_keeps_order_and_rejects_bad_vectors() -> None:
    document_id = uuid4()
    chunks = [_chunk(document_id, text="alpha", index=0), _chunk(document_id, text="beta", index=1)]
    embedder = FakeEmbedder()
    pipeline = EmbeddingPipeline(embedder, 4)
    result = asyncio.run(pipeline.embed(chunks))
    assert [item.chunk.text for item in result.embedded] == ["alpha", "beta"]
    assert result.embedded[0].vector == (1.0, 0.0, 0.0, 0.0)
    assert result.embedded[1].vector == (2.0, 0.0, 0.0, 0.0)
    assert result.errors == ()

    embedder.vectors = [[0.1, 0.2]]
    short = asyncio.run(pipeline.embed(chunks[:1]))
    assert short.embedded == ()
    assert short.errors[0].code == "invalid_vector"
    assert "dimension 2" in short.errors[0].message

    embedder.vectors = [[0.1, 0.2, 0.3, math.nan]]
    nan_result = asyncio.run(pipeline.embed(chunks[:1]))
    assert nan_result.embedded == ()
    assert "non-finite" in nan_result.errors[0].message

    embedder.vectors = [[0.1, 0.2, 0.3, math.inf]]
    infinite = asyncio.run(pipeline.embed(chunks[:1]))
    assert infinite.embedded == ()
    assert vector_problem([], 4) == "Embedding provider returned an empty vector"


def test_provider_errors_do_not_produce_vectors() -> None:
    document_id = uuid4()
    chunk = _chunk(document_id)
    embedder = FakeEmbedder()
    embedder.fail_on = 0
    result = asyncio.run(EmbeddingPipeline(embedder, 4).embed([chunk]))
    assert result.embedded == ()
    assert result.errors[0].code == "embedding_failed"
    assert result.errors[0].chunk_id == chunk.chunk_id
    assert "rate limited" in result.errors[0].message
    assert chunk.text not in result.errors[0].message


def test_payload_uses_the_chunk_id_and_citation_fields() -> None:
    document_id = uuid4()
    chunk = _chunk(document_id, text="PN-100 qty 2")
    embedded = asyncio.run(EmbeddingPipeline(FakeEmbedder(), 4).embed([chunk])).embedded[0]
    point = chunk_point(embedded)
    again = chunk_point(embedded)
    payload = point.to_payload()
    assert point.point_id == str(chunk.chunk_id) == again.point_id
    assert payload["bid_id"] == str(BID)
    assert payload["document_id"] == str(document_id)
    assert payload["chunk_id"] == str(chunk.chunk_id)
    assert payload["file_name"] == "pricing.pdf"
    assert payload["file_type"] == "pdf"
    assert payload["page_number"] == 3
    assert payload["section_title"] == "Pricing"
    assert payload["heading_path"] == ["Pricing", "Schedule"]
    assert payload["chunk_type"] == "table"
    assert payload["chunk_index"] == 0
    assert payload["table_id"] == "table-1"
    assert payload["content_hash"] == "abc123"
    assert payload["document_date"] == "2026-04-01"
    assert payload["addendum_number"] == 2
    assert payload["text"] == "PN-100 qty 2"
    assert payload["bid_id"] != "pricing.pdf"
    json.dumps(payload)
    assert "api_key" not in payload


def test_document_indexing_is_idempotent_and_updates_changed_text() -> None:
    document_id = uuid4()
    other_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_bid(OTHER_BID)
    chunk = _chunk(document_id, text="Original specification")
    other = _chunk(other_id, bid_id=OTHER_BID, text="Leave this document")
    source.add_document(document_id, BID, [chunk])
    source.add_document(other_id, OTHER_BID, [other])
    service, embedder, index = _service(source, batch_size=8)

    first = asyncio.run(service.index_document(document_id))
    second = asyncio.run(service.index_document(document_id))
    chunk_row = source.chunks[document_id][0]
    source.chunks[document_id][0] = IndexChunk(
        chunk_id=chunk_row.chunk_id,
        document_id=chunk_row.document_id,
        bid_id=chunk_row.bid_id,
        chunk_index=chunk_row.chunk_index,
        text="Revised specification",
        content_hash="def456",
        page_number=chunk_row.page_number,
        section_title=chunk_row.section_title,
        chunk_type=chunk_row.chunk_type,
        file_name=chunk_row.file_name,
        file_type=chunk_row.file_type,
        document_date=chunk_row.document_date,
        addendum_number=chunk_row.addendum_number,
        heading_path=chunk_row.heading_path,
        table_id=chunk_row.table_id,
        anchor=chunk_row.anchor,
    )
    third = asyncio.run(service.index_document(document_id))

    stored = index.stored()
    assert first.points_upserted == 1
    assert first.chunks_embedded == 1
    assert first.ingestion_status == "indexed"
    assert second.points_upserted == 1
    assert len(stored) == 1
    assert set(stored) == {str(chunk.chunk_id)}
    assert third.points_upserted == 1
    assert stored[str(chunk.chunk_id)].payload["text"] == "Revised specification"
    assert embedder.calls[-1] == ["Revised specification"]
    assert source.status[other_id] == "pending"
    assert str(other.chunk_id) not in stored
    assert source.history[0] == (document_id, "processing")


def test_empty_chunks_are_skipped_and_batches_stay_ordered() -> None:
    document_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    chunks = [
        _chunk(document_id, text="one", index=0),
        _chunk(document_id, text="   ", index=1),
        _chunk(document_id, text="two", index=2),
        _chunk(document_id, text="three", index=3),
    ]
    source.add_document(document_id, BID, chunks)
    service, embedder, index = _service(source, batch_size=2)
    summary = asyncio.run(service.index_document(document_id))

    assert summary.chunks_requested == 4
    assert summary.chunks_skipped == 1
    assert summary.points_upserted == 3
    assert summary.chunks_failed == 0
    assert [len(call) for call in embedder.calls] == [1, 2]
    assert embedder.calls[0] == ["one"]
    assert embedder.calls[1] == ["two", "three"]
    assert summary.warnings[0].code == "empty_chunk"
    assert source.status[document_id] == "indexed"
    assert len(index.stored()) == 3


def test_embedding_failure_does_not_upsert_that_batch() -> None:
    document_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_document(
        document_id,
        BID,
        [
            _chunk(document_id, text="kept", index=0),
            _chunk(document_id, text="also kept", index=1),
            _chunk(document_id, text="fails", index=2),
        ],
    )
    embedder = FakeEmbedder()
    embedder.fail_on = 1
    service, _embedder, index = _service(source, embedder, batch_size=2)
    summary = asyncio.run(service.index_document(document_id))

    assert summary.points_upserted == 2
    assert summary.chunks_embedded == 2
    assert summary.chunks_failed == 1
    assert summary.errors[0].code == "embedding_failed"
    assert summary.errors[0].chunk_id == source.chunks[document_id][2].chunk_id
    assert summary.ingestion_status == "partial"
    assert source.status[document_id] == "partial"
    assert len(index.stored()) == 2

    embedder.fail_on = None
    retried = asyncio.run(service.index_document(document_id))
    assert retried.points_upserted == 3
    assert retried.chunks_failed == 0
    assert retried.ingestion_status == "indexed"
    assert len(index.stored()) == 3


def test_qdrant_upsert_failure_is_not_counted_as_indexed() -> None:
    document_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_document(document_id, BID, [_chunk(document_id)])
    service, embedder, index = _service(source, index=IndexDouble(fail_upsert=True))
    summary = asyncio.run(service.index_document(document_id))
    assert summary.chunks_embedded == 1
    assert summary.points_upserted == 0
    assert summary.chunks_failed == 1
    assert summary.errors[0].code == "qdrant_upsert"
    assert summary.ingestion_status == "failed"
    assert embedder.calls
    assert index.stored() == {}


def test_collection_dimension_mismatch_indexes_nothing() -> None:
    document_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_document(document_id, BID, [_chunk(document_id, text="SECRET_CHUNK_TEXT")])
    mismatch = IndexDouble(vector_size=8)
    service, embedder, _index = _service(source, index=mismatch)
    summary = asyncio.run(service.index_document(document_id))
    assert summary.points_upserted == 0
    assert summary.chunks_embedded == 0
    assert summary.errors[0].code == "collection_incompatible"
    assert "left unchanged" in summary.errors[0].message
    assert embedder.calls == []
    assert source.status[document_id] == "failed"
    assert "SECRET_CHUNK_TEXT" not in summary.errors[0].message


def test_bid_indexing_leaves_other_bids_alone() -> None:
    first_doc = uuid4()
    second_doc = uuid4()
    outsider = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_bid(OTHER_BID)
    source.add_document(first_doc, BID, [_chunk(first_doc, text="bid one", index=0)])
    source.add_document(second_doc, BID, [_chunk(second_doc, text="bid two", index=0)])
    source.add_document(outsider, OTHER_BID, [_chunk(outsider, bid_id=OTHER_BID, text="other bid")])
    service, _embedder, index = _service(source, batch_size=8)
    summary = asyncio.run(service.index_bid(BID))
    assert summary.bid_id == BID
    assert summary.document_id is None
    assert summary.points_upserted == 2
    assert summary.chunks_failed == 0
    assert source.status[first_doc] == "indexed"
    assert source.status[second_doc] == "indexed"
    assert source.status[outsider] == "pending"
    assert str(source.chunks[outsider][0].chunk_id) not in index.stored()


def test_batch_size_setting_is_bounded() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_BATCH_SIZE"):
        Settings(_env_file=None, embedding_batch_size=0)
    loaded = Settings(_env_file=None)
    assert loaded.embedding_batch_size == 32


def test_upsert_uses_the_configured_collection() -> None:
    document_id = uuid4()
    source = MemorySource()
    source.add_bid(BID)
    source.add_document(document_id, BID, [_chunk(document_id)])
    service, _embedder, index = _service(source)
    asyncio.run(service.index_document(document_id))
    upserts = [call for call in index.client.calls if call[0] == "upsert"]
    assert upserts[0][1] == "unit_chunks"
    assert len(upserts[0][2][0].vector) == 4
    assert not any(call[0] == "create" for call in index.client.calls)
    with pytest.raises(CollectionDimensionError):
        IndexDouble(vector_size=9).ensure_collection()
