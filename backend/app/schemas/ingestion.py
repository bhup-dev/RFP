"""Results from registering source files. Parsing is not included."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class IngestionOutcome(str, Enum):
    """What happened to one discovered path during registration."""

    REGISTERED = "registered"
    DUPLICATE = "duplicate"
    CHANGED = "changed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class IngestionFileResult(BaseModel):
    """One file outcome. Paths are relative to the configured input root."""

    relative_path: str
    document_type: str | None = None
    file_hash: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    outcome: IngestionOutcome
    document_id: UUID | None = None
    bid_identifier: str | None = None
    message: str | None = None


class IngestionSummary(BaseModel):
    """Aggregate registration result for one scan."""

    input_root: str
    files_discovered: int = Field(ge=0)
    files_registered: int = Field(ge=0)
    duplicates_skipped: int = Field(ge=0)
    changed_files: int = Field(ge=0)
    unsupported_files: int = Field(ge=0)
    failed_files: int = Field(ge=0)
    results: list[IngestionFileResult]


class IngestionScanRequest(BaseModel):
    """A directory under the configured input root.

    ``bid_identifier`` overrides folder grouping for this scan. It is the
    internal grouping id, not an official solicitation number.
    """

    relative_directory: str | None = None
    bid_identifier: str | None = Field(default=None, max_length=128)
