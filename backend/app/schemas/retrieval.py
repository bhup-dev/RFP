"""Request and response contracts for hybrid chunk retrieval."""

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import JsonValue


class RetrievalMode(str, Enum):
    """Backends that contributed to a response."""

    HYBRID = "hybrid"
    SEMANTIC_ONLY = "semantic_only"
    KEYWORD_ONLY = "keyword_only"


class RetrievalRequest(BaseModel):
    """A search over chunks, optionally limited to one bid or document set."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=2000)
    bid_id: UUID | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    file_types: list[str] = Field(default_factory=list)
    chunk_types: list[str] = Field(default_factory=list)
    top_k: int = Field(
        default=8,
        ge=1,
        le=50,
        validation_alias=AliasChoices("top_k", "limit"),
    )
    semantic_candidates: int = Field(default=30, ge=1, le=200)
    keyword_candidates: int = Field(default=30, ge=1, le=200)
    # Compatibility with the Task 1.4 contract. New callers use plural filters.
    chunk_type: str | None = Field(default=None, max_length=64, exclude=True)
    file_name: str | None = Field(default=None, max_length=512, exclude=True)

    @field_validator("query")
    @classmethod
    def query_is_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("file_types", "chunk_types")
    @classmethod
    def filter_values_are_non_blank(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip().lower()
            if not item:
                raise ValueError("filter values must not be blank")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("document_ids")
    @classmethod
    def document_ids_are_unique(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def merge_legacy_filters(self) -> "RetrievalRequest":
        if self.chunk_type:
            item = self.chunk_type.strip().lower()
            if item and item not in self.chunk_types:
                self.chunk_types.append(item)
        return self

    @property
    def limit(self) -> int:
        """Compatibility alias for callers created before hybrid retrieval."""
        return self.top_k


class RetrievedChunk(BaseModel):
    """One retrieved passage and the scores that selected it."""

    bid_id: UUID
    document_id: UUID
    chunk_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=512)
    chunk_type: str | None = Field(default=None, max_length=64)
    semantic_score: float | None = Field(default=None, allow_inf_nan=False)
    keyword_score: float | None = Field(default=None, ge=0.0)
    fusion_score: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    # Kept for downstream Task 1.4 schemas. Task 2.5 never sets this field.
    reranker_score: float | None = None


class AppliedFilters(BaseModel):
    """Normalized filters applied to both retrieval backends."""

    bid_id: UUID | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    file_types: list[str] = Field(default_factory=list)
    chunk_types: list[str] = Field(default_factory=list)
    file_name: str | None = None


class RetrievalResponse(BaseModel):
    """Ranked chunks and non-sensitive retrieval diagnostics."""

    query: str
    bid_id: UUID | None = None
    results: list[RetrievedChunk] = Field(default_factory=list)
    total_results: int = Field(default=0, ge=0)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    applied_filters: AppliedFilters = Field(default_factory=AppliedFilters)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    rewriting_applied: bool = False
    rewritten_query_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    reranking_applied: bool = False
    final_result_count: int = Field(default=0, ge=0)
