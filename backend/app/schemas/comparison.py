"""Structured contracts for evidence-grounded multi-bid comparison."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.citation import Citation
from app.schemas.common import ConfidenceScore, JsonValue


class ComparisonDimension(str, Enum):
    SCOPE = "scope"
    PRODUCTS = "products"
    COMMERCIAL_TERMS = "commercial_terms"
    COMPLIANCE = "compliance"
    ADDENDA = "addenda"


class DifferenceClassification(str, Enum):
    EQUIVALENT = "equivalent"
    DIFFERENT = "different"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    NOT_COMPARABLE = "not_comparable"
    UNCERTAIN = "uncertain"


class ComparisonStatus(str, Enum):
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class BidComparisonRequest(BaseModel):
    bid_ids: list[UUID] = Field(min_length=2)
    dimensions: list[ComparisonDimension] = Field(
        default_factory=lambda: list(ComparisonDimension)
    )
    extraction_run_ids: dict[UUID, UUID] = Field(default_factory=dict)
    allow_incomplete_profiles: bool = False

    @field_validator("bid_ids")
    @classmethod
    def bid_ids_are_distinct(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("bid_ids must contain distinct values")
        return values

    @field_validator("dimensions")
    @classmethod
    def dimensions_are_distinct(
        cls, values: list[ComparisonDimension]
    ) -> list[ComparisonDimension]:
        if not values:
            raise ValueError("at least one comparison dimension is required")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def run_references_match_requested_bids(self) -> "BidComparisonRequest":
        unknown = set(self.extraction_run_ids) - set(self.bid_ids)
        if unknown:
            raise ValueError("extraction_run_ids contains an unrequested bid")
        return self


class ComparisonProfileReference(BaseModel):
    bid_id: UUID
    bid_identifier: str
    profile_id: UUID
    extraction_run_id: UUID | None = None
    addendum_analysis_run_id: UUID | None = None
    validation_run_id: UUID | None = None
    profile_status: str


class PerBidComparisonValue(BaseModel):
    bid_id: UUID
    original_value: JsonValue = None
    normalized_value: JsonValue = None
    field_status: str
    supporting_citations: list[Citation] = Field(default_factory=list)
    contradicting_citations: list[Citation] = Field(default_factory=list)
    addendum_changes: list[dict[str, Any]] = Field(default_factory=list)


class ComparisonRow(BaseModel):
    dimension: ComparisonDimension
    attribute: str
    values: list[PerBidComparisonValue]
    classification: DifferenceClassification
    explanation: str
    confidence: ConfidenceScore | None = None
    requires_human_review: bool = False
    review_recommendation: str


class ComparisonSummary(BaseModel):
    total_rows: int = Field(ge=0)
    equivalent: int = Field(ge=0)
    different: int = Field(ge=0)
    missing: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    not_comparable: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class BidComparisonReport(BaseModel):
    comparison_id: UUID
    status: ComparisonStatus
    bid_ids: list[UUID]
    profile_references: list[ComparisonProfileReference]
    comparison_dimensions: list[ComparisonDimension]
    summary: ComparisonSummary
    comparison_rows: list[ComparisonRow]
    key_similarities: list[str] = Field(default_factory=list)
    key_differences: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    requires_human_review: bool
    created_at: datetime


class LLMSemanticComparison(BaseModel):
    attribute: str
    classification: DifferenceClassification
    explanation: str = Field(min_length=1, max_length=2000)
    confidence: ConfidenceScore | None = None

    @field_validator("classification")
    @classmethod
    def semantic_classification_is_bounded(
        cls, value: DifferenceClassification
    ) -> DifferenceClassification:
        if value not in {
            DifferenceClassification.EQUIVALENT,
            DifferenceClassification.DIFFERENT,
            DifferenceClassification.NOT_COMPARABLE,
            DifferenceClassification.UNCERTAIN,
        }:
            raise ValueError("semantic comparison cannot override evidence status")
        return value


class LLMSemanticComparisonOutput(BaseModel):
    comparisons: list[LLMSemanticComparison] = Field(
        default_factory=list, max_length=30
    )
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def attributes_are_unique(self) -> "LLMSemanticComparisonOutput":
        attributes = [item.attribute for item in self.comparisons]
        if len(attributes) != len(set(attributes)):
            raise ValueError("semantic comparison attributes must be unique")
        return self
