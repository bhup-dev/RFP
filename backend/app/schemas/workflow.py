"""End-to-end ingestion and indexing workflow summaries."""

from uuid import UUID

from pydantic import BaseModel, Field


class EndToEndRequest(BaseModel):
    """A directory under the configured ingestion root."""

    relative_directory: str | None = None
    bid_identifier: str | None = Field(default=None, max_length=128)


class WorkflowDocumentResult(BaseModel):
    """Stage outcomes for one source document."""

    document_id: UUID | None = None
    bid_id: UUID | None = None
    relative_path: str
    registration_outcome: str | None = None
    parsing: str = "not_run"
    chunking: str = "not_run"
    indexing: str = "not_run"
    chunks_created: int = Field(default=0, ge=0)
    chunks_skipped: int = Field(default=0, ge=0)
    points_upserted: int = Field(default=0, ge=0)
    resumed_from: str | None = None
    completed: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EndToEndSummary(BaseModel):
    """Aggregate for a root, bid folder, or one registered document."""

    files_discovered: int = Field(default=0, ge=0)
    documents_completed: int = Field(default=0, ge=0)
    documents_failed: int = Field(default=0, ge=0)
    documents_skipped: int = Field(default=0, ge=0)
    chunks_created: int = Field(default=0, ge=0)
    points_upserted: int = Field(default=0, ge=0)
    results: list[WorkflowDocumentResult] = Field(default_factory=list)


class CitationFailure(BaseModel):
    chunk_id: UUID
    reason: str


class CitationValidationResult(BaseModel):
    checked: int = Field(ge=0)
    resolved: int = Field(ge=0)
    failures: list[CitationFailure] = Field(default_factory=list)


class ConsistencyFailure(BaseModel):
    chunk_id: UUID
    code: str
    message: str


class ConsistencyReport(BaseModel):
    chunks_checked: int = Field(ge=0)
    points_found: int = Field(ge=0)
    failures: list[ConsistencyFailure] = Field(default_factory=list)
