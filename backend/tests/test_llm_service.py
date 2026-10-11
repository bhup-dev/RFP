"""LLM service tests. The SDK client is mocked and no provider is contacted."""

import asyncio
import logging
from types import SimpleNamespace

import httpx2
import pytest
from openai import (
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from app.services.llm_service import LLMService
from app.services.provider_errors import (
    ProviderConfigurationError,
    ProviderRequestError,
)

SECRET = "sk-test-secret"
PROMPT = "Summarize the delivery requirements in section 4."


def _settings(**overrides: object):
    from app.config import settings

    values: dict[str, object] = {
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "openai_api_key": SECRET,
    }
    values.update(overrides)
    return settings.model_copy(update=values)


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=_request())


class FakeResponses:
    def __init__(self, output_text: object = "Delivery is due in 30 days.") -> None:
        self.calls: list[dict[str, object]] = []
        self.output_text = output_text
        self.error: Exception | None = None

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text: object = "Delivery is due in 30 days.") -> None:
        self.responses = FakeResponses(output_text)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_generate_response_returns_text_for_the_configured_model() -> None:
    client = FakeClient()
    service = LLMService(client=client, settings_obj=_settings(llm_model="gpt-4o-mini"))

    text = asyncio.run(
        service.generate_response(
            PROMPT,
            system="Answer from the supplied prompt.",
            temperature=0,
            max_output_tokens=32,
        )
    )

    assert text == "Delivery is due in 30 days."
    sent = client.responses.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert sent["input"] == PROMPT
    assert sent["instructions"] == "Answer from the supplied prompt."
    assert sent["temperature"] == 0
    assert sent["max_output_tokens"] == 32


def test_generate_response_omits_unset_generation_parameters() -> None:
    client = FakeClient()
    service = LLMService(client=client, settings_obj=_settings())
    asyncio.run(service.generate_response("What is the bid number?"))
    sent = client.responses.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert "instructions" not in sent
    assert "temperature" not in sent
    assert "max_output_tokens" not in sent


def test_gpt_5_omits_unsupported_temperature_parameter() -> None:
    client = FakeClient()
    service = LLMService(client=client, settings_obj=_settings(llm_model="gpt-5.5"))

    asyncio.run(
        service.generate_response(
            PROMPT,
            temperature=0,
            max_output_tokens=32,
        )
    )

    sent = client.responses.calls[0]
    assert sent["model"] == "gpt-5.5"
    assert "temperature" not in sent
    assert sent["max_output_tokens"] == 32


def test_missing_credentials_fail_before_a_client_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.llm_service as module

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OpenAI client was constructed")

    monkeypatch.setattr(module, "AsyncOpenAI", fail_if_called)
    service = LLMService(settings_obj=_settings(openai_api_key=None))
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        asyncio.run(service.generate_response(PROMPT))


def test_unsupported_provider_is_rejected() -> None:
    client = FakeClient()
    service = LLMService(client=client, settings_obj=_settings(llm_provider="anthropic"))
    with pytest.raises(ProviderConfigurationError, match="Unsupported LLM provider"):
        asyncio.run(service.generate_response(PROMPT))
    assert client.responses.calls == []


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (APITimeoutError(_request()), "timed out"),
        (
            RateLimitError(
                f"slow down {SECRET} {PROMPT}",
                response=_response(429),
                body=None,
            ),
            "rate limited",
        ),
        (
            AuthenticationError(
                f"bad key {SECRET}",
                response=_response(401),
                body=None,
            ),
            "OPENAI_API_KEY",
        ),
        (
            InternalServerError(
                f"upstream failed {SECRET} {PROMPT}",
                response=_response(500),
                body=None,
            ),
            "HTTP 500",
        ),
    ],
)
def test_provider_errors_are_safe(
    error: Exception,
    match: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()
    client.responses.error = error
    service = LLMService(client=client, settings_obj=_settings())
    with caplog.at_level(logging.ERROR), pytest.raises(ProviderRequestError, match=match) as raised:
        asyncio.run(service.generate_response(PROMPT))
    assert SECRET not in str(raised.value)
    assert PROMPT not in str(raised.value)
    assert SECRET not in caplog.text
    assert PROMPT not in caplog.text


def test_empty_provider_response_is_rejected() -> None:
    client = FakeClient(output_text="   ")
    service = LLMService(client=client, settings_obj=_settings())
    with pytest.raises(ProviderRequestError, match="empty response"):
        asyncio.run(service.generate_response(PROMPT))


def test_malformed_provider_response_is_rejected() -> None:
    client = FakeClient(output_text=None)
    service = LLMService(client=client, settings_obj=_settings())
    with pytest.raises(ProviderRequestError, match="empty response"):
        asyncio.run(service.generate_response(PROMPT))


def test_empty_prompt_is_rejected_before_the_provider() -> None:
    client = FakeClient()
    service = LLMService(client=client, settings_obj=_settings())
    with pytest.raises(ValueError, match="prompt"):
        asyncio.run(service.generate_response("  "))
    assert client.responses.calls == []


def test_client_is_created_once_and_not_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.llm_service as module

    created: list[dict[str, object]] = []

    class ConstructedClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.responses = FakeResponses()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(module, "AsyncOpenAI", ConstructedClient)
    service = LLMService(settings_obj=_settings())
    assert created == []

    async def _call_twice() -> None:
        await service.generate_response("first")
        await service.generate_response("second")

    asyncio.run(_call_twice())
    assert len(created) == 1
    assert created[0]["api_key"] == SECRET
    assert created[0]["max_retries"] == 0
    assert service.client.responses.calls[0]["model"] == "gpt-4o-mini"
