"""Semantic retrieval uses one query embedding and validates Qdrant payloads."""

import asyncio
from uuid import uuid4

from app.retrieval.query_service import prepare_query
from app.retrieval.semantic_search import SemanticSearch
from app.schemas.retrieval import RetrievalRequest
from app.services.qdrant_service import VectorHit


class Embedder:
    def __init__(self, vector=None) -> None:
        self.vector = vector or [0.1, 0.2, 0.3, 0.4]
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [self.vector]


class Index:
    def __init__(self, hits) -> None:
        self.hits = hits
        self.calls = []

    def search(self, vector, **kwargs):
        self.calls.append((list(vector), kwargs))
        return self.hits


def _payload(chunk_id, document_id, bid_id):
    return {
        "bid_id": str(bid_id),
        "document_id": str(document_id),
        "chunk_id": str(chunk_id),
        "file_name": "pricing.pdf",
        "file_type": "pdf",
        "page_number": 4,
        "section_title": "Pricing",
        "chunk_type": "table",
        "text": "WD22TB4 | 2",
        "heading_path": ["Pricing"],
        "table_id": "table-1",
        "chunk_index": 3,
    }


def test_query_is_embedded_once_and_filters_reach_qdrant() -> None:
    bid_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    embedder = Embedder()
    index = Index(
        [VectorHit(point_id=str(chunk_id), score=0.8, payload=_payload(chunk_id, document_id, bid_id))]
    )
    search = SemanticSearch(embedder, index, 4)
    query = prepare_query(
        RetrievalRequest(
            query="docking station",
            bid_id=bid_id,
            document_ids=[document_id],
            file_types=["PDF"],
            chunk_types=["TABLE"],
            semantic_candidates=12,
        )
    )

    result = asyncio.run(search.search(query))

    assert embedder.calls == [["docking station"]]
    assert len(index.calls) == 1
    assert index.calls[0][1]["bid_id"] == bid_id
    assert index.calls[0][1]["document_ids"] == (document_id,)
    assert index.calls[0][1]["file_types"] == ("pdf",)
    assert index.calls[0][1]["chunk_types"] == ("table",)
    assert index.calls[0][1]["limit"] == 12
    candidate = result.candidates[0]
    assert candidate.chunk_id == chunk_id
    assert candidate.semantic_score == 0.8
    assert candidate.metadata["table_id"] == "table-1"


def test_malformed_payloads_are_skipped_with_warning() -> None:
    chunk_id = uuid4()
    index = Index(
        [
            VectorHit(
                point_id=str(chunk_id),
                score=0.8,
                payload={"chunk_id": str(chunk_id), "text": "missing source ids"},
            )
        ]
    )
    result = asyncio.run(
        SemanticSearch(Embedder(), index, 4).search(
            prepare_query(RetrievalRequest(query="requirements"))
        )
    )
    assert result.candidates == ()
    assert "malformed semantic result" in result.warnings[0]


def test_invalid_query_vector_fails_before_qdrant() -> None:
    index = Index([])
    search = SemanticSearch(Embedder([0.1, 0.2]), index, 4)
    try:
        asyncio.run(search.search(prepare_query(RetrievalRequest(query="requirements"))))
    except Exception as exc:
        assert getattr(exc, "backend", None) == "semantic"
        assert "dimension 2" in str(exc)
    else:
        raise AssertionError("invalid vector was accepted")
    assert index.calls == []
