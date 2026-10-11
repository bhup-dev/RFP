"""Bounded LLM query rewriting with safe original-query fallback."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.keyword_search import identifier_terms

_DATE_OR_NUMBER = re.compile(
    r"(?i)(?:\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b|\b\d+(?:\.\d+)?%?\b)"
)
_WORDS = re.compile(r"(?u)\b[\w-]{3,}\b")
_STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "the",
    "they",
    "what",
    "when",
    "where",
    "which",
    "with",
}


class QueryLLM(Protocol):
    async def generate_response(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        """Generate a response through the application's configured LLM."""


class RewritePayload(BaseModel):
    """Only this JSON shape is accepted from the LLM."""

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class RewriteResult:
    """Original query followed by unique, validated alternatives."""

    queries: tuple[str, ...]
    applied: bool
    warning: str | None = None


class QueryRewriter:
    """Generate search variants without replacing protected source terms."""

    def __init__(
        self,
        llm: QueryLLM,
        *,
        enabled: bool,
        max_variants: int,
        timeout_seconds: float,
        max_query_length: int = 2000,
    ) -> None:
        if max_variants < 1:
            raise ValueError("max_variants must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._llm = llm
        self._enabled = enabled
        self._max_variants = max_variants
        self._timeout = timeout_seconds
        self._max_length = max_query_length

    async def rewrite(self, original: str) -> RewriteResult:
        """Always return the unchanged original as the first query."""
        if not self._enabled:
            return RewriteResult(queries=(original,), applied=False)
        try:
            raw = await asyncio.wait_for(
                self._llm.generate_response(
                    _prompt(original, self._max_variants),
                    system=(
                        "You create procurement search queries. Return only valid JSON. "
                        "Preserve identifiers, dates, quantities, and contractual terms. "
                        "Never answer the question or add unsupported facts."
                    ),
                    temperature=0.0,
                    max_output_tokens=400,
                ),
                timeout=self._timeout,
            )
            payload = RewritePayload.model_validate(_json_object(raw))
            alternatives = self._validated(original, payload.queries)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return RewriteResult(
                queries=(original,),
                applied=False,
                warning="Query rewriting failed; searched the original query only",
            )
        if not alternatives:
            return RewriteResult(
                queries=(original,),
                applied=False,
                warning="Query rewriting produced no valid alternatives",
            )
        return RewriteResult(
            queries=(original, *alternatives),
            applied=True,
        )

    def _validated(self, original: str, generated: list[str]) -> tuple[str, ...]:
        protected = _protected_terms(original)
        intent = _intent_terms(original)
        seen = {_normalized(original)}
        accepted: list[str] = []
        for value in generated:
            if not isinstance(value, str):
                continue
            query = " ".join(value.split())
            normalized = _normalized(query)
            if (
                not query
                or len(query) > self._max_length
                or normalized in seen
                or any(term.casefold() not in query.casefold() for term in protected)
                or (
                    intent
                    and not any(term in query.casefold() for term in intent)
                )
            ):
                continue
            seen.add(normalized)
            accepted.append(query)
            if len(accepted) == self._max_variants:
                break
        return tuple(accepted)


def _prompt(original: str, count: int) -> str:
    return json.dumps(
        {
            "task": (
                "Produce alternative search queries for hybrid retrieval. "
                "Keep the same intent and every exact identifier, date, number, "
                "quantity, and contractual term. Do not provide an answer."
            ),
            "original_query": original,
            "maximum_alternatives": count,
            "output_schema": {"queries": ["string"]},
        },
        ensure_ascii=False,
    )


def _json_object(raw: str) -> object:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1])
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid rewrite JSON") from exc
    return value


def _protected_terms(query: str) -> tuple[str, ...]:
    values = [*identifier_terms(query)]
    values.extend(match.group(0) for match in _DATE_OR_NUMBER.finditer(query))
    return tuple(dict.fromkeys(value.casefold() for value in values))


def _normalized(query: str) -> str:
    return " ".join(query.casefold().split())


def _intent_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            word.casefold()
            for word in _WORDS.findall(query)
            if word.casefold() not in _STOP_WORDS
        )
    )
