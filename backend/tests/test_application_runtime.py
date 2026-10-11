"""Configuration, logging, request ids, and readiness. External services are mocked."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, settings
from app.core.context import (
    bind_operation_id,
    get_operation_id,
    get_request_id,
    reset_operation_id,
)
from app.core.health import postgresql_check, qdrant_check
from app.core.logging import HANDLER_NAME, ContextFilter, JsonFormatter, configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.storage import ensure_storage_directory
from app.core.validation import ConfigurationError, validate_configuration
from app.main import app
from app.services.qdrant_service import QdrantReadiness

SECRET_KEY = "sk-config-secret"
SECRET_PASSWORD = "super-secret-pass"
QDRANT_SECRET = "qd-secret-key"
VALID_REQUEST_ID = "req-aaaa-1111"
OTHER_REQUEST_ID = "req-bbbb-2222"


def _receive():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


def _scope(request_id: str | None) -> dict[str, object]:
    headers: list[tuple[bytes, bytes]] = []
    if request_id is not None:
        headers.append((b"x-request-id", request_id.encode("latin-1")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }


async def _invoke(inner, request_id: str | None) -> tuple[int, str, bytes]:
    captured: dict[str, object] = {"status": 0, "header": "", "body": b""}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            for key, value in message["headers"]:
                if key == b"x-request-id":
                    captured["header"] = value.decode("ascii")
        elif message["type"] == "http.response.body":
            captured["body"] = message.get("body", b"")

    await RequestContextMiddleware(inner)(_scope(request_id), _receive(), send)
    return (
        int(captured["status"]),
        str(captured["header"]),
        bytes(captured["body"]),
    )


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("HEALTH_CHECK_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "3072")
    monkeypatch.setenv("ENVIRONMENT", "production")

    loaded = Settings(_env_file=None)

    assert loaded.debug is True
    assert loaded.log_level == "DEBUG"
    assert loaded.log_format == "json"
    assert loaded.request_timeout_seconds == 15
    assert loaded.health_check_timeout_seconds == 1
    assert loaded.llm_model == "gpt-4o-mini"
    assert loaded.embedding_model == "text-embedding-3-large"
    assert loaded.embedding_dimension == 3072
    assert loaded.llm_model != loaded.embedding_model


def test_production_log_format_defaults_to_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_FORMAT", "auto")
    loaded = Settings(_env_file=None)
    assert loaded.log_format == "json"


def test_invalid_configuration_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", SECRET_KEY)
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)
    message = str(raised.value)
    assert "LOG_LEVEL" in message
    assert SECRET_KEY not in message

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("HEALTH_CHECK_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValidationError, match="timeout"):
        Settings(_env_file=None)

    invalid = settings.model_copy(update={"llm_provider": "anthropic"})
    with pytest.raises(ConfigurationError, match="openai"):
        validate_configuration(invalid)


def test_startup_rejects_an_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    with pytest.raises(ConfigurationError, match="openai"):
        with TestClient(app):
            pass


def test_production_rejects_default_development_database_credentials() -> None:
    production = Settings(_env_file=None, environment="production")
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        validate_configuration(production)


def test_missing_provider_credentials_are_valid_at_startup() -> None:
    validate_configuration(settings.model_copy(update={"openai_api_key": None}))


def test_public_view_omits_secrets() -> None:
    loaded = settings.model_copy(
        update={
            "database_url": (
                "postgresql+asyncpg://postgres:"
                f"{SECRET_PASSWORD}@localhost:5432/rfp_intelligence"
            ),
            "openai_api_key": SECRET_KEY,
            "qdrant_api_key": QDRANT_SECRET,
        }
    )
    rendered = str(loaded.public_view())
    assert SECRET_PASSWORD not in rendered
    assert SECRET_KEY not in rendered
    assert QDRANT_SECRET not in rendered
    assert "***" in loaded.public_view()["database_url"]
    assert SECRET_PASSWORD not in repr(loaded)
    assert SECRET_KEY not in repr(loaded)


def test_log_setup_is_idempotent_and_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", SECRET_KEY)
    configure_logging(settings)
    configure_logging(settings)
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "name", None) == HANDLER_NAME
    ]
    assert len(handlers) == 1

    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider key %s",
        args=(SECRET_KEY,),
        exc_info=None,
    )
    assert ContextFilter().filter(record) is True
    assert SECRET_KEY not in record.getMessage()
    assert "***" in record.getMessage()


def test_json_logs_include_correlation_ids() -> None:
    token = bind_operation_id("op-12345678")
    request_token = None
    try:
        from app.core.context import set_request_id

        request_token = set_request_id(VALID_REQUEST_ID)
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="ready",
            args=(),
            exc_info=None,
        )
        ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_operation_id(token)
        if request_token is not None:
            from app.core.context import reset_request_id

            reset_request_id(request_token)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "ready"
    assert payload["request_id"] == VALID_REQUEST_ID
    assert payload["operation_id"] == "op-12345678"
    assert "timestamp" in payload
    assert get_request_id() is None
    assert get_operation_id() is None


def test_generated_request_id_is_returned() -> None:
    response = TestClient(app).get("/health")
    request_id = response.headers["x-request-id"]
    UUID(request_id)
    assert response.json()["status"] == "ok"


def test_valid_request_id_is_propagated() -> None:
    response = TestClient(app).get("/health", headers={"X-Request-ID": VALID_REQUEST_ID})
    assert response.headers["x-request-id"] == VALID_REQUEST_ID


@pytest.mark.parametrize("incoming", ["bad id", "x" * 300, "short", "../secret"])
def test_malformed_request_id_is_replaced(incoming: str) -> None:
    response = TestClient(app).get("/health", headers={"X-Request-ID": incoming})
    request_id = response.headers["x-request-id"]
    assert request_id != incoming
    UUID(request_id)


def test_concurrent_requests_do_not_share_request_ids() -> None:
    def fetch(value: str) -> str:
        response = TestClient(app).get("/health", headers={"X-Request-ID": value})
        return response.headers["x-request-id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(fetch, [VALID_REQUEST_ID, OTHER_REQUEST_ID])
    assert first == VALID_REQUEST_ID
    assert second == OTHER_REQUEST_ID

    async def inner(scope, receive, send):
        del scope, receive
        first = get_request_id()
        await asyncio.sleep(0.02)
        assert get_request_id() == first
        body = (first or "").encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    async def both():
        return await asyncio.gather(
            _invoke(inner, VALID_REQUEST_ID),
            _invoke(inner, OTHER_REQUEST_ID),
        )

    results = asyncio.run(both())
    assert results[0][2] == VALID_REQUEST_ID.encode()
    assert results[1][2] == OTHER_REQUEST_ID.encode()
    assert get_request_id() is None


def test_request_context_resets_when_the_request_fails() -> None:
    async def inner(scope, receive, send):
        del scope, receive, send
        raise RuntimeError("secret-document-text")

    with pytest.raises(RuntimeError, match="secret-document-text"):
        asyncio.run(_invoke(inner, VALID_REQUEST_ID))
    assert get_request_id() is None

    mini = FastAPI()
    mini.add_middleware(RequestContextMiddleware)

    @mini.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-document-text")

    response = TestClient(mini, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert "secret-document-text" not in response.text
    assert "Traceback" not in response.text
    UUID(response.headers["x-request-id"])


def test_health_stays_available_when_postgresql_cannot_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine was created")

    monkeypatch.setattr("app.db.session.create_async_engine", fail_if_called)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_each_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    async def postgres_ok() -> dict[str, str]:
        return {"status": "ok"}

    async def qdrant_down() -> dict[str, str]:
        return {"status": "unavailable", "detail": "Collection unit_chunks does not exist"}

    monkeypatch.setattr("app.core.health.postgresql_check", postgres_ok)
    monkeypatch.setattr("app.core.health.qdrant_check", qdrant_down)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["postgresql"] == {"status": "ok"}
    assert body["qdrant"]["status"] == "unavailable"
    assert "unit_chunks" in body["qdrant"]["detail"]
    assert "Traceback" not in response.text

    async def qdrant_ok() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.setattr("app.core.health.qdrant_check", qdrant_ok)
    ready = TestClient(app).get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "postgresql": {"status": "ok"},
        "qdrant": {"status": "ok"},
    }


def test_dependency_failures_hide_secrets_and_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = (
        "postgresql+asyncpg://postgres:"
        f"{SECRET_PASSWORD}@localhost:5432/rfp_intelligence"
    )

    def explode():
        raise RuntimeError(f"connect failed {url}\nTraceback")

    monkeypatch.setattr("app.core.health.get_engine", explode)
    postgres = asyncio.run(postgresql_check())
    assert postgres == {"status": "unavailable", "detail": "PostgreSQL is unavailable"}
    assert SECRET_PASSWORD not in str(postgres)
    assert "Traceback" not in str(postgres)

    monkeypatch.setattr(settings, "qdrant_api_key", QDRANT_SECRET)

    class Unavailable:
        def readiness(self) -> QdrantReadiness:
            return QdrantReadiness(
                connected=False,
                collection_ready=False,
                detail=f"rejected {QDRANT_SECRET}",
            )

    monkeypatch.setattr("app.core.health.get_qdrant_service", lambda: Unavailable())
    qdrant = asyncio.run(qdrant_check())
    assert qdrant["status"] == "unavailable"
    assert QDRANT_SECRET not in str(qdrant)
    assert "Traceback" not in str(qdrant)


def test_lifespan_closes_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def dispose() -> None:
        events.append("engine")

    def close_qdrant() -> None:
        events.append("qdrant")

    async def close_llm() -> None:
        events.append("llm")

    async def close_embedding() -> None:
        events.append("embedding")

    monkeypatch.setattr("app.main.dispose_engine", dispose)
    monkeypatch.setattr("app.main.close_qdrant_client", close_qdrant)
    monkeypatch.setattr("app.main.close_llm_client", close_llm)
    monkeypatch.setattr("app.main.close_embedding_client", close_embedding)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        events.append("running")

    assert events == ["running", "engine", "qdrant", "llm", "embedding"]


def test_storage_directory_is_created_when_requested(tmp_path) -> None:
    created = ensure_storage_directory(tmp_path / "incoming")
    assert created.is_dir()
