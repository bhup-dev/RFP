"""Async text generation through a configured LLM provider.

Importing this module does not create a client or contact the provider.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.config import Settings, settings
from app.services.provider_errors import (
    ProviderConfigurationError,
    ProviderRequestError,
    describe_provider_error,
)

logger = logging.getLogger(__name__)

SUPPORTED_LLM_PROVIDERS = ("openai",)


class LLMService:
    """Reusable completion client. The model comes from settings."""

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        settings_obj: Settings | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._settings = settings_obj or settings

    @property
    def client(self) -> AsyncOpenAI:
        """Return the shared async client, creating it on first use."""
        self._require_ready()
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key,
                timeout=self._settings.request_timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def close(self) -> None:
        """Close a client this service created. Injected clients stay open."""
        client = self._client
        if self._owns_client:
            self._client = None
        if client is not None and self._owns_client:
            await client.close()

    async def generate_response(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        """Return the model's text for a user prompt and optional system message."""
        self._require_ready()
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must not be empty")
        if system is not None and (not isinstance(system, str) or not system.strip()):
            raise ValueError("system message must not be empty")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")

        request: dict[str, Any] = {
            "model": self._settings.llm_model,
            "input": prompt,
        }
        if system is not None:
            request["instructions"] = system
        if temperature is not None and _supports_temperature(
            self._settings.llm_model
        ):
            request["temperature"] = temperature
        if max_output_tokens is not None:
            request["max_output_tokens"] = max_output_tokens

        try:
            response = await self.client.responses.create(**request)
        except OpenAIError as exc:
            error = describe_provider_error(exc, "LLM request")
            logger.error("%s", error)
            raise error from exc

        text = getattr(response, "output_text", None)
        if not isinstance(text, str) or not text.strip():
            logger.error("LLM provider returned an empty response")
            raise ProviderRequestError("LLM provider returned an empty response")
        return text

    def _require_ready(self) -> None:
        provider = self._settings.llm_provider.strip().lower()
        if provider not in SUPPORTED_LLM_PROVIDERS:
            raise ProviderConfigurationError(
                f"Unsupported LLM provider {self._settings.llm_provider!r}. "
                f"Supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
            )
        if self._client is None and not self._settings.openai_api_key:
            raise ProviderConfigurationError(
                "OPENAI_API_KEY is required to call the OpenAI LLM provider. "
                "Set OPENAI_API_KEY in the environment."
            )


def _supports_temperature(model: str) -> bool:
    """GPT-5 reasoning models reject the Responses API temperature parameter."""
    return not model.strip().lower().startswith("gpt-5")


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Return the process-wide service. Does not connect until a method runs."""
    global _service
    if _service is None:
        _service = LLMService()
    return _service


async def close_llm_client() -> None:
    """Close the shared client if one was created."""
    global _service
    if _service is None:
        return
    await _service.close()
    _service = None
