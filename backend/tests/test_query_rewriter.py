"""Query rewriting preserves originals and protected procurement identifiers."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.retrieval.query_rewriter import QueryRewriter


class LLM:
    def __init__(self, output: str = '{"queries": []}') -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    async def generate_response(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.error:
            raise self.error
        return self.output


def _rewriter(llm: LLM, **overrides) -> QueryRewriter:
    values = {
        "enabled": True,
        "max_variants": 3,
        "timeout_seconds": 1.0,
        "max_query_length": 200,
    }
    values.update(overrides)
    return QueryRewriter(llm, **values)


def test_valid_queries_are_parsed_original_first_and_deduplicated() -> None:
    llm = LLM(
        json.dumps(
            {
                "queries": [
                    "Dell laptop warranty duration",
                    " dell   laptop warranty duration ",
                    "manufacturer Dell laptop warranty support",
                ]
            }
        )
    )
    result = asyncio.run(
        _rewriter(llm).rewrite("What is the Dell laptop warranty?")
    )
    assert result.queries == (
        "What is the Dell laptop warranty?",
        "Dell laptop warranty duration",
        "manufacturer Dell laptop warranty support",
    )
    assert result.applied is True
    assert len(llm.calls) == 1
    assert llm.calls[0]["temperature"] == 0.0


def test_exact_identifiers_dates_and_quantities_must_remain() -> None:
    llm = LLM(
        json.dumps(
            {
                "queries": [
                    "warranty obligations",
                    "WD22TB4 warranty 24 months by 10/31/2026",
                ]
            }
        )
    )
    original = "WD22TB4 warranty for 24 months due 10/31/2026"
    result = asyncio.run(_rewriter(llm).rewrite(original))
    assert result.queries[0] == original
    assert result.queries[1:] == (
        "WD22TB4 warranty 24 months by 10/31/2026",
    )


def test_malformed_output_and_provider_failure_fall_back() -> None:
    original = "JA-207652 delivery requirements"
    malformed = asyncio.run(_rewriter(LLM("not json")).rewrite(original))
    assert malformed.queries == (original,)
    assert malformed.applied is False
    assert "failed" in malformed.warning.lower()

    llm = LLM()
    llm.error = RuntimeError("provider unavailable with secret text")
    failed = asyncio.run(_rewriter(llm).rewrite(original))
    assert failed.queries == (original,)
    assert failed.warning == "Query rewriting failed; searched the original query only"
    assert "secret" not in failed.warning


def test_disabled_rewriting_never_calls_llm() -> None:
    llm = LLM('{"queries": ["alternative"]}')
    result = asyncio.run(_rewriter(llm, enabled=False).rewrite("original"))
    assert result.queries == ("original",)
    assert result.applied is False
    assert llm.calls == []


def test_variant_count_and_length_are_bounded() -> None:
    llm = LLM(
        json.dumps(
            {
                "queries": [
                    "original one valid",
                    "original two valid",
                    "original three valid",
                    "x" * 201,
                ]
            }
        )
    )
    result = asyncio.run(
        _rewriter(llm, max_variants=2).rewrite("original question")
    )
    assert result.queries == (
        "original question",
        "original one valid",
        "original two valid",
    )


def test_enhancement_settings_are_validated() -> None:
    loaded = Settings(_env_file=None, openai_api_key=None)
    assert loaded.query_rewriting_enabled is True
    assert loaded.reranking_enabled is False
    assert loaded.query_rewrite_max_variants == 3
    assert loaded.rerank_candidate_limit == 30
    for update in (
        {"query_rewrite_max_variants": 0},
        {"query_rewrite_timeout_seconds": 0},
        {"rerank_candidate_limit": 201},
        {"rerank_batch_size": 0},
        {"rerank_timeout_seconds": 0},
        {"rerank_max_input_characters": 100},
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **update)
