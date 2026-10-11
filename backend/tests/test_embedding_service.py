"""Embedding service tests. The SDK client is mocked and no provider is contacted."""

import asyncio
import logging
from types import SimpleNamespace

import httpx2
import pytest
from openai import APITimeoutError, InternalServerError

from app.services.embedding_service import EmbeddingService
from app.services.provider_errors import ProviderConfigurationError, ProviderRequestError

SECRET = "sk-embed-secret"
PASSAGE = "Payment terms are net 30."


def _settings(**overrides: object):
    from app.config import settings

    values: dict[str, object] = {
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 4,
        "openai_api_key": SECRET,
        "llm_model": "gpt-4o-mini",
    }
    values.update(overrides)
    return settings.model_copy(update=values)


class EmbeddingItem:
    def __init__(self, index: int, embedding: object) -> None:
        self.index = index
        self.embedding = embedding


class FakeEmbeddings:
    def __init__(
        self,
        vectors: list[object] | None = None,
        *,
        reverse: bool = False,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.vectors = vectors if vectors is not None else [[0.1, 0.2, 0.3, 0.4]]
        self.reverse = reverse
        self.error: Exception | None = None

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        inputs = kwargs.get("input")
        count = len(inputs) if isinstance(inputs, list) else len(self.vectors)
        source = self.vectors
        if len(source) == 1 and count > 1:
            source = source * count
        items = [EmbeddingItem(index, source[index]) for index in range(count)]
        if self.reverse:
            items.reverse()
        return SimpleNamespace(data=items)


class FakeClient:
    def __init__(self, embeddings: FakeEmbeddings | None = None) -> None:
        self.embeddings = embeddings or FakeEmbeddings()


def test_embed_text_returns_the_configured_dimension() -> None:
    client = FakeClient()
    service = EmbeddingService(client=client, settings_obj=_settings())
    vector = asyncio.run(service.embed_text(PASSAGE))
    assert vector == [0.1, 0.2, 0.3, 0.4]
    sent = client.embeddings.calls[0]
    assert sent["model"] == "text-embedding-3-small"
    assert sent["input"] == [PASSAGE]
    assert sent["dimensions"] == 4


def test_embed_texts_preserves_input_order_in_one_request() -> None:
    texts = ["alpha", "beta", "gamma"]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    client = FakeClient(FakeEmbeddings(vectors, reverse=True))
    service = EmbeddingService(client=client, settings_obj=_settings())
    result = asyncio.run(service.embed_texts(texts))
    assert result == vectors
    assert len(client.embeddings.calls) == 1
    assert client.embeddings.calls[0]["input"] == texts


def test_empty_batch_does_not_call_the_provider() -> None:
    client = FakeClient()
    service = EmbeddingService(client=client, settings_obj=_settings())
    assert asyncio.run(service.embed_texts([])) == []
    assert client.embeddings.calls == []


def test_empty_text_is_rejected() -> None:
    client = FakeClient()
    service = EmbeddingService(client=client, settings_obj=_settings())
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(service.embed_text("  "))
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(service.embed_texts(["ok", ""]))
    assert client.embeddings.calls == []


def test_dimensions_parameter_is_limited_to_compatible_models() -> None:
    client = FakeClient()
    service = EmbeddingService(
        client=client,
        settings_obj=_settings(
            embedding_model="text-embedding-ada-002",
            embedding_dimension=4,
        ),
    )
    asyncio.run(service.embed_text(PASSAGE))
    assert "dimensions" not in client.embeddings.calls[0]
    assert client.embeddings.calls[0]["model"] == "text-embedding-ada-002"


def test_invalid_vector_dimension_is_rejected() -> None:
    client = FakeClient(FakeEmbeddings([[0.1, 0.2]]))
    service = EmbeddingService(client=client, settings_obj=_settings(embedding_dimension=4))
    with pytest.raises(ProviderRequestError, match="EMBEDDING_DIMENSION is 4"):
        asyncio.run(service.embed_text(PASSAGE))


def test_empty_vector_is_rejected() -> None:
    client = FakeClient(FakeEmbeddings([[]]))
    service = EmbeddingService(client=client, settings_obj=_settings())
    with pytest.raises(ProviderRequestError, match="empty vector"):
        asyncio.run(service.embed_text(PASSAGE))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_vector_is_rejected(invalid: float) -> None:
    client = FakeClient(FakeEmbeddings([[0.1, invalid, 0.3, 0.4]]))
    service = EmbeddingService(client=client, settings_obj=_settings())
    with pytest.raises(ProviderRequestError, match="non-finite vector"):
        asyncio.run(service.embed_text(PASSAGE))


def test_missing_credentials_fail_before_a_client_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.embedding_service as module

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OpenAI client was constructed")

    monkeypatch.setattr(module, "AsyncOpenAI", fail_if_called)
    service = EmbeddingService(settings_obj=_settings(openai_api_key=None))
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        asyncio.run(service.embed_text(PASSAGE))


def test_unsupported_provider_is_rejected() -> None:
    client = FakeClient()
    service = EmbeddingService(client=client, settings_obj=_settings(embedding_provider="cohere"))
    with pytest.raises(ProviderConfigurationError, match="Unsupported embedding provider"):
        asyncio.run(service.embed_text(PASSAGE))
    assert client.embeddings.calls == []


def test_provider_errors_do_not_return_vectors(caplog: pytest.LogCaptureFixture) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
    client = FakeClient()
    client.embeddings.error = InternalServerError(
        f"upstream failed {SECRET} {PASSAGE}",
        response=httpx2.Response(500, request=request),
        body=None,
    )
    service = EmbeddingService(client=client, settings_obj=_settings())
    with caplog.at_level(logging.ERROR), pytest.raises(ProviderRequestError, match="HTTP 500") as raised:
        asyncio.run(service.embed_texts([PASSAGE, "second passage"]))
    assert SECRET not in str(raised.value)
    assert PASSAGE not in str(raised.value)
    assert SECRET not in caplog.text
    assert PASSAGE not in caplog.text


def test_timeout_is_reported_without_a_vector() -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
    client = FakeClient()
    client.embeddings.error = APITimeoutError(request)
    service = EmbeddingService(client=client, settings_obj=_settings())
    with pytest.raises(ProviderRequestError, match="timed out"):
        asyncio.run(service.embed_text(PASSAGE))


def test_client_is_created_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.embedding_service as module

    created: list[dict[str, object]] = []

    class ConstructedClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.embeddings = FakeEmbeddings()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(module, "AsyncOpenAI", ConstructedClient)
    service = EmbeddingService(settings_obj=_settings(embedding_model="text-embedding-3-large"))
    assert created == []

    async def _call_twice() -> None:
        await service.embed_text("one")
        await service.embed_texts(["two", "three"])

    asyncio.run(_call_twice())
    assert len(created) == 1
    assert created[0]["max_retries"] == 0
    assert service.client.embeddings.calls[0]["model"] == "text-embedding-3-large"
    assert service.client.embeddings.calls[1]["input"] == ["two", "three"]
