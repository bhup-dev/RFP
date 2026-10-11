"""Qdrant service tests. The client is mocked and no server is required."""

import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Headers
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams

from app.config import settings
from app.main import app
from app.services.qdrant_service import (
    ChunkPoint,
    CollectionDimensionError,
    QdrantService,
    build_chunk_filter,
)

BID_A = UUID("11111111-1111-1111-1111-111111111111")
BID_B = UUID("22222222-2222-2222-2222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-4444-444444444444")


class RecordingClient:
    def __init__(self, *, exists: bool = False, vector_size: int = 4) -> None:
        self.exists = exists
        self.vector_size = vector_size
        self.calls: list[tuple[object, ...]] = []
        self.deleted = False

    def collection_exists(self, collection_name: str) -> bool:
        self.calls.append(("exists", collection_name))
        return self.exists

    def create_collection(self, collection_name: str, vectors_config: VectorParams) -> bool:
        self.calls.append(
            ("create", collection_name, vectors_config.size, vectors_config.distance)
        )
        self.exists = True
        return True

    def delete_collection(self, collection_name: str) -> bool:
        self.deleted = True
        raise AssertionError(f"deleted {collection_name}")

    def get_collections(self) -> object:
        self.calls.append(("get_collections",))
        return object()

    def get_collection(self, collection_name: str) -> object:
        self.calls.append(("get_collection", collection_name))
        vectors = SimpleNamespace(size=self.vector_size)
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    def upsert(self, collection_name: str, points: list[object], wait: bool = True) -> object:
        self.calls.append(("upsert", collection_name, points, wait))
        return object()

    def query_points(self, **kwargs: object) -> object:
        self.calls.append(("query_points", kwargs))
        return SimpleResponse([])

    def close(self) -> None:
        self.calls.append(("close",))


class SimplePoint:
    def __init__(self, point_id: str, score: float, payload: dict[str, object]) -> None:
        self.id = point_id
        self.score = score
        self.payload = payload


class SimpleResponse:
    def __init__(self, points: list[SimplePoint]) -> None:
        self.points = points


def _settings(dimension: int = 4):
    return settings.model_copy(
        update={
            "embedding_dimension": dimension,
            "qdrant_collection_name": "unit_chunks",
            "qdrant_api_key": None,
        }
    )


def _point(chunk_id: UUID = CHUNK_ID, bid_id: UUID = BID_A) -> ChunkPoint:
    return ChunkPoint(
        bid_id=bid_id,
        document_id=DOCUMENT_ID,
        chunk_id=chunk_id,
        vector=(0.1, 0.2, 0.3, 0.4),
        file_name="addendum_1.pdf",
        text="Relevant extracted document passage",
        page_number=4,
        section_title="Submission Requirements",
        chunk_type="text",
        metadata={},
    )


def test_payload_uses_stable_uuid_strings() -> None:
    point = _point()
    payload = point.to_payload()
    assert point.point_id == str(CHUNK_ID)
    assert payload["chunk_id"] == point.point_id
    assert payload["bid_id"] == str(BID_A)
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["file_name"] == "addendum_1.pdf"
    assert payload["page_number"] == 4
    assert payload["section_title"] == "Submission Requirements"
    assert payload["chunk_type"] == "text"
    assert payload["text"] == "Relevant extracted document passage"
    assert payload["metadata"] == {}
    assert "api_key" not in payload
    assert _point(chunk_id=CHUNK_ID).point_id == point.point_id


def test_ensure_collection_creates_once_with_configured_dimension() -> None:
    client = RecordingClient(exists=False, vector_size=8)
    service = QdrantService(client=client, settings_obj=_settings(8))
    service.ensure_collection()
    client.exists = True
    service.ensure_collection()
    creates = [call for call in client.calls if call[0] == "create"]
    assert creates == [("create", "unit_chunks", 8, Distance.COSINE)]
    assert client.deleted is False


def test_ensure_collection_treats_conflict_as_already_present() -> None:
    class ConflictClient(RecordingClient):
        def collection_exists(self, collection_name: str) -> bool:
            self.calls.append(("exists", collection_name))
            return len([call for call in self.calls if call[0] == "create"]) > 0

        def create_collection(self, collection_name: str, vectors_config: VectorParams) -> bool:
            self.calls.append(("create", collection_name))
            raise UnexpectedResponse(409, "Conflict", b"already exists", Headers())

    service = QdrantService(client=ConflictClient(), settings_obj=_settings())
    service.ensure_collection()


def test_ensure_collection_rejects_existing_size_mismatch() -> None:
    client = RecordingClient(exists=True, vector_size=1536)
    service = QdrantService(client=client, settings_obj=_settings(8))
    with pytest.raises(CollectionDimensionError, match="left unchanged"):
        service.ensure_collection()
    assert not any(call[0] == "create" for call in client.calls)
    assert client.deleted is False


def test_upsert_rejects_collection_size_mismatch_before_write() -> None:
    client = RecordingClient(exists=True, vector_size=8)
    service = QdrantService(client=client, settings_obj=_settings(4))
    with pytest.raises(CollectionDimensionError, match="EMBEDDING_DIMENSION"):
        service.upsert([_point()])
    assert not any(call[0] == "upsert" for call in client.calls)


def test_upsert_rejects_non_finite_values() -> None:
    client = RecordingClient()
    service = QdrantService(client=client, settings_obj=_settings(4))
    point = _point()
    invalid = ChunkPoint(
        bid_id=point.bid_id,
        document_id=point.document_id,
        chunk_id=point.chunk_id,
        vector=(0.1, 0.2, 0.3, float("nan")),
        file_name=point.file_name,
        text=point.text,
    )
    with pytest.raises(ValueError, match="finite"):
        service.upsert([invalid])
    assert client.calls == []


def test_upsert_rejects_wrong_dimension_before_request() -> None:
    client = RecordingClient()
    service = QdrantService(client=client, settings_obj=_settings(4))
    point = _point()
    wrong = ChunkPoint(
        bid_id=point.bid_id,
        document_id=point.document_id,
        chunk_id=point.chunk_id,
        vector=(1.0, 2.0),
        file_name=point.file_name,
        text=point.text,
    )
    with pytest.raises(ValueError, match="dimension"):
        service.upsert([wrong])
    assert client.calls == []


def test_upsert_uses_chunk_id_as_point_id() -> None:
    client = RecordingClient()
    service = QdrantService(client=client, settings_obj=_settings())
    service.upsert([_point()])
    service.upsert([_point()])
    upserts = [call for call in client.calls if call[0] == "upsert"]
    assert len(upserts) == 2
    first_id = upserts[0][2][0].id
    second_id = upserts[1][2][0].id
    assert first_id == second_id == str(CHUNK_ID)
    assert upserts[0][2][0].payload["bid_id"] == str(BID_A)


def test_filter_is_optional_and_composable() -> None:
    assert build_chunk_filter() is None
    only_bid = build_chunk_filter(bid_id=BID_A)
    assert only_bid is not None
    assert len(only_bid.must) == 1
    assert only_bid.must[0].key == "bid_id"
    assert only_bid.must[0].match.value == str(BID_A)
    assert str(BID_B) not in str(only_bid.must[0].match.value)

    combined = build_chunk_filter(
        bid_id=BID_A,
        document_id=DOCUMENT_ID,
        chunk_type="text",
        file_name="addendum_1.pdf",
    )
    assert combined is not None
    assert {condition.key for condition in combined.must} == {
        "bid_id",
        "document_id",
        "chunk_type",
        "file_name",
    }


def test_search_sends_query_vector_and_bid_filter() -> None:
    client = RecordingClient()
    other_bid = str(BID_B)
    client.query_points = lambda **kwargs: (  # type: ignore[method-assign]
        client.calls.append(("query_points", kwargs)) or SimpleResponse(
            [SimplePoint(str(CHUNK_ID), 0.91, {"bid_id": str(BID_A), "text": "passage"})]
        )
    )
    service = QdrantService(client=client, settings_obj=_settings())
    hits = service.search((0.1, 0.2, 0.3, 0.4), limit=3, bid_id=BID_A)
    assert hits[0].point_id == str(CHUNK_ID)
    assert hits[0].score == 0.91
    assert hits[0].payload["bid_id"] == str(BID_A)
    sent = client.calls[-1][1]
    assert sent["query"] == [0.1, 0.2, 0.3, 0.4]
    assert sent["limit"] == 3
    assert sent["query_filter"].must[0].match.value == str(BID_A)
    assert other_bid != sent["query_filter"].must[0].match.value


def test_search_rejects_wrong_dimension_before_request() -> None:
    client = RecordingClient()
    service = QdrantService(client=client, settings_obj=_settings())
    with pytest.raises(ValueError, match="dimension"):
        service.search((1.0,), bid_id=BID_A)
    assert client.calls == []


def test_readiness_reports_connection_failure_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenClient:
        def get_collections(self) -> object:
            raise ConnectionError("connection refused to qdrant")

    service = QdrantService(client=BrokenClient(), settings_obj=_settings())
    with caplog.at_level(logging.ERROR):
        result = service.readiness()
    assert result.connected is False
    assert result.collection_ready is False
    assert result.ready is False
    assert "connection refused" in (result.detail or "")
    assert "connection refused" in caplog.text
    assert "Traceback" not in caplog.text


def test_readiness_log_omits_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    secret = "qd-secret-value"

    class BrokenClient:
        def get_collections(self) -> object:
            raise ConnectionError(f"rejected {secret}")

    loaded = _settings().model_copy(update={"qdrant_api_key": secret})
    service = QdrantService(client=BrokenClient(), settings_obj=loaded)
    with caplog.at_level(logging.ERROR):
        result = service.readiness()
    assert secret not in (result.detail or "")
    assert secret not in caplog.text
    assert "Traceback" not in caplog.text


def test_readiness_does_not_create_a_missing_collection() -> None:
    client = RecordingClient(exists=False)
    service = QdrantService(client=client, settings_obj=_settings())
    result = service.readiness()
    assert result.connected is True
    assert result.collection_ready is False
    assert not any(call[0] == "create" for call in client.calls)


def test_client_is_not_created_until_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.qdrant_service as module

    created: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr(module, "QdrantClient", FakeClient)
    service = module.QdrantService(settings_obj=_settings())
    assert created == []
    service.client
    assert created[0]["url"] == _settings().qdrant_url
    assert created[0]["api_key"] is None
    assert created[0]["prefer_grpc"] is False
    assert created[0]["check_compatibility"] is False


def test_health_does_not_construct_qdrant_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.qdrant_service as module

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Qdrant client was constructed")

    monkeypatch.setattr(module, "QdrantClient", fail_if_called)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_qdrant_health_endpoint_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Unavailable:
        def readiness(self) -> object:
            from app.services.qdrant_service import QdrantReadiness

            return QdrantReadiness(
                connected=False,
                collection_ready=False,
                detail="connection refused",
            )

    monkeypatch.setattr("app.main.get_qdrant_service", lambda: Unavailable())
    response = TestClient(app).get("/health/qdrant")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["connected"] is False
    assert body["detail"] == "connection refused"
    assert body["collection"] == settings.qdrant_collection_name


def test_invalid_identifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="UUID"):
        build_chunk_filter(bid_id="not-a-uuid")


def test_blank_api_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "   ")
    from app.config import Settings

    loaded = Settings(_env_file=None)
    assert loaded.qdrant_api_key is None
