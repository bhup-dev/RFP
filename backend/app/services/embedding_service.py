"""Async text embeddings through a configured provider.

The model and expected dimension are the same settings used by Qdrant.
Importing this module does not create a client or contact the provider.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.config import Settings, settings
from app.services.provider_errors import (
    ProviderConfigurationError,
    ProviderRequestError,
    describe_provider_error,
)

logger = logging.getLogger(__name__)

SUPPORTED_EMBEDDING_PROVIDERS = ("openai",)


class EmbeddingService:
    """Reusable embedding client. Vectors are checked against the configured size."""

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

    async def embed_text(self, text: str) -> list[float]:
        """Return one embedding for a single non-empty string."""
        vectors = await self.embed_texts([text])
        return vectors[0]

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per input, in the same order, from one request."""
        if isinstance(texts, str):
            raise TypeError("embed_texts expects a sequence of strings")
        items = list(texts)
        if not items:
            return []
        for text in items:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("each text must be a non-empty string")

        self._require_ready()
        request: dict[str, Any] = {
            "model": self._settings.embedding_model,
            "input": items,
        }
        if _model_accepts_dimensions(self._settings.embedding_model):
            request["dimensions"] = self._settings.embedding_dimension

        try:
            response = await self.client.embeddings.create(**request)
        except OpenAIError as exc:
            error = describe_provider_error(exc, "Embedding request")
            logger.error("%s", error)
            raise error from exc

        return self._vectors_in_input_order(getattr(response, "data", None), len(items))

    def _vectors_in_input_order(self, data: object, count: int) -> list[list[float]]:
        if not isinstance(data, list) or len(data) != count:
            logger.error("Embedding provider returned an unexpected number of vectors")
            raise ProviderRequestError(
                "Embedding provider returned an unexpected number of vectors"
            )
        try:
            indexes = [item.index for item in data]
        except AttributeError as exc:
            logger.error("Embedding provider returned a response without indexes")
            raise ProviderRequestError(
                "Embedding provider returned a response without indexes"
            ) from exc
        if sorted(indexes) != list(range(count)):
            logger.error("Embedding provider returned indexes that do not match the input")
            raise ProviderRequestError(
                "Embedding provider returned indexes that do not match the input"
            )

        expected = self._settings.embedding_dimension
        ordered = sorted(data, key=lambda item: item.index)
        vectors: list[list[float]] = []
        for item in ordered:
            raw = getattr(item, "embedding", None)
            if not isinstance(raw, list) or not raw:
                logger.error("Embedding provider returned an empty vector")
                raise ProviderRequestError("Embedding provider returned an empty vector")
            try:
                vector = [float(value) for value in raw]
            except (TypeError, ValueError) as exc:
                logger.error("Embedding provider returned a non-numeric vector")
                raise ProviderRequestError(
                    "Embedding provider returned a non-numeric vector"
                ) from exc
            if not all(math.isfinite(value) for value in vector):
                logger.error("Embedding provider returned a non-finite vector")
                raise ProviderRequestError(
                    "Embedding provider returned a non-finite vector"
                )
            if len(vector) != expected:
                logger.error(
                    "Embedding provider returned dimension %s; EMBEDDING_DIMENSION is %s",
                    len(vector),
                    expected,
                )
                raise ProviderRequestError(
                    f"Embedding provider returned dimension {len(vector)}, "
                    f"but EMBEDDING_DIMENSION is {expected}. "
                    "Set EMBEDDING_DIMENSION to the model output size. "
                    "If a Qdrant collection already uses a different size, "
                    "set QDRANT_COLLECTION_NAME to a new collection created at "
                    f"size {expected}. The existing collection is left unchanged."
                )
            vectors.append(vector)
        return vectors

    def _require_ready(self) -> None:
        provider = self._settings.embedding_provider.strip().lower()
        if provider not in SUPPORTED_EMBEDDING_PROVIDERS:
            raise ProviderConfigurationError(
                f"Unsupported embedding provider {self._settings.embedding_provider!r}. "
                f"Supported providers: {', '.join(SUPPORTED_EMBEDDING_PROVIDERS)}."
            )
        if self._client is None and not self._settings.openai_api_key:
            raise ProviderConfigurationError(
                "OPENAI_API_KEY is required to call the OpenAI embedding provider. "
                "Set OPENAI_API_KEY in the environment."
            )


def _model_accepts_dimensions(model: str) -> bool:
    """OpenAI text-embedding-3 models accept an output dimension."""
    return model.startswith("text-embedding-3")


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide service. Does not connect until a method runs."""
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service


async def close_embedding_client() -> None:
    """Close the shared client if one was created."""
    global _service
    if _service is None:
        return
    await _service.close()
    _service = None
