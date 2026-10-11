"""Structured bid extraction contracts and provider-response validation."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.citation import Citation
from app.schemas.common import AwareDatetime, ConfidenceScore, JsonValue

ASSIGNMENT_FIELD_NAMES: tuple[str, ...] = (
    "Bid Number",
    "Title",
    "Due Date",
    "Bid Submission Type",
    "Term of Bid",
    "Pre Bid Meeting",
    "Installation",
    "Bid Bond Requirement",
    "Delivery Date",
    "Payment Terms",
    "Additional Documentation Required",
    "MFG for Registration",
    "Contract or Cooperative to use",
    "Model_no",
    "Part_no",
    "Product",
    "contact_info",
    "company_name",
    "Bid Summary",
    "Product Specification",
)


class FieldValidationStatus(str, Enum):
    """Outcome of checking one extracted field. Unset means not checked yet."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


class LLMFieldStatus(str, Enum):
    """Statuses accepted from the bounded structured extraction prompt."""

    EXTRACTED = "extracted"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"


class ExtractionRunStatus(str, Enum):
    """Persisted lifecycle state for one extraction attempt."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ExtractedField(BaseModel):
    """One extracted value and the evidence attached to it."""

    value: JsonValue = None
    confidence: ConfidenceScore | None = None
    citations: list[Citation] = Field(default_factory=list)
    notes: str | None = None
    validation_status: FieldValidationStatus | None = None


class BidExtraction(BaseModel):
    """The 20 assignment fields. Missing information stays null."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    bid_number: ExtractedField | None = Field(default=None, alias="Bid Number")
    title: ExtractedField | None = Field(default=None, alias="Title")
    due_date: ExtractedField | None = Field(default=None, alias="Due Date")
    bid_submission_type: ExtractedField | None = Field(default=None, alias="Bid Submission Type")
    term_of_bid: ExtractedField | None = Field(default=None, alias="Term of Bid")
    pre_bid_meeting: ExtractedField | None = Field(default=None, alias="Pre Bid Meeting")
    installation: ExtractedField | None = Field(default=None, alias="Installation")
    bid_bond_requirement: ExtractedField | None = Field(default=None, alias="Bid Bond Requirement")
    delivery_date: ExtractedField | None = Field(default=None, alias="Delivery Date")
    payment_terms: ExtractedField | None = Field(default=None, alias="Payment Terms")
    additional_documentation_required: ExtractedField | None = Field(
        default=None,
        alias="Additional Documentation Required",
    )
    mfg_for_registration: ExtractedField | None = Field(default=None, alias="MFG for Registration")
    contract_or_cooperative_to_use: ExtractedField | None = Field(
        default=None,
        alias="Contract or Cooperative to use",
    )
    model_no: ExtractedField | None = Field(default=None, alias="Model_no")
    part_no: ExtractedField | None = Field(default=None, alias="Part_no")
    product: ExtractedField | None = Field(default=None, alias="Product")
    contact_info: ExtractedField | None = Field(default=None, alias="contact_info")
    company_name: ExtractedField | None = Field(default=None, alias="company_name")
    bid_summary: ExtractedField | None = Field(default=None, alias="Bid Summary")
    product_specification: ExtractedField | None = Field(
        default=None,
        alias="Product Specification",
    )


class LLMExtractedField(BaseModel):
    """Untrusted field claim returned by the LLM before citation resolution."""

    model_config = ConfigDict(extra="forbid")

    value: JsonValue = None
    confidence: ConfidenceScore | None = None
    chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    status: LLMFieldStatus

    @model_validator(mode="after")
    def evidence_matches_status(self) -> "LLMExtractedField":
        if self.status == LLMFieldStatus.NOT_FOUND:
            if self.value is not None:
                raise ValueError("not_found fields must have a null value")
            if self.chunk_ids:
                raise ValueError("not_found fields must not cite chunks")
        elif self.status == LLMFieldStatus.EXTRACTED:
            if self.value is None:
                raise ValueError("extracted fields must have a non-null value")
            if not self.chunk_ids:
                raise ValueError("extracted fields must cite at least one chunk")
        elif not self.chunk_ids:
            raise ValueError("ambiguous and conflicting fields must cite evidence")
        return self


class LLMExtractionPayload(BaseModel):
    """Strict provider payload containing every assignment field exactly once."""

    model_config = ConfigDict(extra="forbid")

    bid_number: LLMExtractedField = Field(alias="Bid Number")
    title: LLMExtractedField = Field(alias="Title")
    due_date: LLMExtractedField = Field(alias="Due Date")
    bid_submission_type: LLMExtractedField = Field(alias="Bid Submission Type")
    term_of_bid: LLMExtractedField = Field(alias="Term of Bid")
    pre_bid_meeting: LLMExtractedField = Field(alias="Pre Bid Meeting")
    installation: LLMExtractedField = Field(alias="Installation")
    bid_bond_requirement: LLMExtractedField = Field(alias="Bid Bond Requirement")
    delivery_date: LLMExtractedField = Field(alias="Delivery Date")
    payment_terms: LLMExtractedField = Field(alias="Payment Terms")
    additional_documentation_required: LLMExtractedField = Field(
        alias="Additional Documentation Required"
    )
    mfg_for_registration: LLMExtractedField = Field(alias="MFG for Registration")
    contract_or_cooperative_to_use: LLMExtractedField = Field(
        alias="Contract or Cooperative to use"
    )
    model_no: LLMExtractedField = Field(alias="Model_no")
    part_no: LLMExtractedField = Field(alias="Part_no")
    product: LLMExtractedField = Field(alias="Product")
    contact_info: LLMExtractedField = Field(alias="contact_info")
    company_name: LLMExtractedField = Field(alias="company_name")
    bid_summary: LLMExtractedField = Field(alias="Bid Summary")
    product_specification: LLMExtractedField = Field(alias="Product Specification")

    def claims_by_external_name(self) -> dict[str, LLMExtractedField]:
        """Return claims keyed by the assignment's exact external names."""
        return {
            str(field.alias): getattr(self, name)
            for name, field in type(self).model_fields.items()
        }


class ExtractionResponse(BaseModel):
    """Public result for one persisted extraction attempt."""

    run_id: UUID
    bid_id: UUID
    status: ExtractionRunStatus
    model_name: str
    document_ids: list[UUID]
    started_at: AwareDatetime
    completed_at: AwareDatetime
    fields: BidExtraction
