"""Optional live Qdrant check.

Skipped when QDRANT_URL cannot be reached. This test does not use an
in-memory Qdrant client.
"""

from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient

from app.config import settings
from app.services.qdrant_service import ChunkPoint, QdrantService

pytestmark = pytest.mark.qdrant


def test_live_upsert_and_bid_filter() -> None:
    collection_name = f"rfp_document_chunks_test_{uuid4().hex[:8]}"
    test_settings = settings.model_copy(
        update={
            "qdrant_collection_name": collection_name,
            "embedding_dimension": 4,
        }
    )
    client: QdrantClient | None = None
    try:
        client = QdrantClient(
            url=test_settings.qdrant_url,
            api_key=test_settings.qdrant_api_key,
            timeout=2,
            prefer_grpc=False,
            check_compatibility=False,
        )
        client.get_collections()
    except Exception as exc:
        if client is not None:
            client.close()
        pytest.skip(f"Qdrant is not available ({type(exc).__name__})")

    assert client is not None
    service = QdrantService(client=client, settings_obj=test_settings)
    bid_a = uuid4()
    bid_b = uuid4()
    created = False
    try:
        service.ensure_collection()
        created = True
        service.ensure_collection()
        service.upsert(
            [
                _live_point(bid_a, "bid a passage"),
                _live_point(bid_b, "bid b passage"),
            ]
        )
        hits = service.search((0.1, 0.2, 0.3, 0.4), limit=5, bid_id=bid_a)
        assert hits
        assert {hit.payload["bid_id"] for hit in hits} == {str(bid_a)}
    finally:
        if created:
            client.delete_collection(collection_name)
        client.close()


def _live_point(bid_id: UUID, text: str) -> ChunkPoint:
    return ChunkPoint(
        bid_id=bid_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        vector=(0.1, 0.2, 0.3, 0.4),
        file_name="addendum_1.pdf",
        text=text,
        page_number=1,
        section_title="Submission Requirements",
        chunk_type="text",
    )
