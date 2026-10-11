"""Optional lazy cross-encoder reranking.

``sentence-transformers`` is an optional runtime dependency. No model is
imported or downloaded at module import or application startup.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.schemas.retrieval import RetrievedChunk


class CrossEncoderModel(Protocol):
    def predict(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
    ) -> Sequence[float]:
        """Return one relevance score per query-passage pair."""


class RerankerProvider(Protocol):
    def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        batch_size: int,
    ) -> list[float]:
        """Return one cross-encoder score per passage."""


class SentenceTransformersProvider:
    """Lazily share one CrossEncoder instance per configured model."""

    _models: dict[str, CrossEncoderModel] = {}
    _lock = threading.Lock()

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        batch_size: int,
    ) -> list[float]:
        model = self._model()
        raw = model.predict(
            [(query, passage) for passage in passages],
            batch_size=batch_size,
            show_progress_bar=False,
        )
        return [float(value) for value in raw]

    def _model(self) -> CrossEncoderModel:
        existing = self._models.get(self._model_name)
        if existing is not None:
            return existing
        with self._lock:
            existing = self._models.get(self._model_name)
            if existing is not None:
                return existing
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed"
                ) from exc
            model = CrossEncoder(self._model_name)
            self._models[self._model_name] = model
            return model


@dataclass(frozen=True)
class RerankResult:
    results: tuple[RetrievedChunk, ...]
    applied: bool
    candidate_count: int
    warnings: tuple[str, ...] = ()


class Reranker:
    """Score a bounded RRF candidate set against the unchanged user query."""

    def __init__(
        self,
        provider: RerankerProvider,
        *,
        enabled: bool,
        candidate_limit: int,
        batch_size: int,
        timeout_seconds: float,
        max_input_characters: int,
    ) -> None:
        if candidate_limit < 1 or batch_size < 1:
            raise ValueError("reranker limits must be positive")
        if timeout_seconds <= 0 or max_input_characters < 1:
            raise ValueError("reranker timeout and input limit must be positive")
        self._provider = provider
        self._enabled = enabled
        self._candidate_limit = candidate_limit
        self._batch_size = batch_size
        self._timeout = timeout_seconds
        self._max_characters = max_input_characters

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def candidate_limit(self) -> int:
        return self._candidate_limit

    async def rerank(
        self,
        original_query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_k: int,
    ) -> RerankResult:
        pool = list(candidates[: self._candidate_limit])
        if not self._enabled or not pool:
            return RerankResult(
                results=tuple(pool[:top_k]),
                applied=False,
                candidate_count=len(pool),
            )
        passages: list[str] = []
        truncated = False
        for candidate in pool:
            prepared, was_truncated = _model_input(
                candidate.text, self._max_characters
            )
            passages.append(prepared)
            truncated = truncated or was_truncated
        try:
            scores = await asyncio.wait_for(
                asyncio.to_thread(
                    self._provider.score,
                    original_query,
                    passages,
                    batch_size=self._batch_size,
                ),
                timeout=self._timeout,
            )
            values = _validated_scores(scores, len(pool))
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return RerankResult(
                results=tuple(pool[:top_k]),
                applied=False,
                candidate_count=len(pool),
                warnings=(
                    "Reranking failed; retained reciprocal-rank fusion order",
                ),
            )

        scored = [
            (candidate.model_copy(update={"reranker_score": score}), index)
            for index, (candidate, score) in enumerate(
                zip(pool, values, strict=True)
            )
        ]
        scored.sort(key=lambda item: (-float(item[0].reranker_score), item[1]))
        warnings = (
            (
                "Some reranker inputs kept their beginning and end because they exceeded "
                "RERANK_MAX_INPUT_CHARACTERS; returned chunk text is unchanged"
            ),
        ) if truncated else ()
        return RerankResult(
            results=tuple(item[0] for item in scored[:top_k]),
            applied=True,
            candidate_count=len(pool),
            warnings=warnings,
        )


def build_reranker_provider(provider: str, model: str) -> RerankerProvider:
    if provider.strip().lower() != "sentence_transformers":
        raise ValueError(f"Unsupported reranker provider {provider!r}")
    return SentenceTransformersProvider(model)


def _model_input(text: str, maximum: int) -> tuple[str, bool]:
    if len(text) <= maximum:
        return text, False
    marker = "\n[...]\n"
    available = max(0, maximum - len(marker))
    beginning = (available + 1) // 2
    end = available // 2
    return text[:beginning] + marker + (text[-end:] if end else ""), True


def _validated_scores(scores: object, expected: int) -> list[float]:
    if isinstance(scores, str) or not isinstance(scores, Sequence):
        raise ValueError("reranker returned invalid scores")
    if len(scores) != expected:
        raise ValueError("reranker returned an unexpected score count")
    values = [float(value) for value in scores]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("reranker returned a non-finite score")
    return values
