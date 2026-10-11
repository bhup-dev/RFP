"""Shared types for API and workflow schemas."""

import math
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field


def ensure_json_value(value: object) -> object:
    """Accept only JSON-compatible values: null, bool, number, string, array, object."""
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be a finite number")
        return value
    if isinstance(value, list):
        return [ensure_json_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: ensure_json_value(item) for key, item in value.items()}
    raise ValueError("value must be JSON-compatible")


JsonValue = Annotated[Any, BeforeValidator(ensure_json_value)]


def require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(require_timezone)]
ConfidenceScore = Annotated[float, Field(ge=0.0, le=1.0)]
UnitScore = Annotated[float, Field(ge=0.0, le=1.0)]


class ProcessingStatus(str, Enum):
    """Coarse status shared by ingestion and indexing.

    ``pending`` is the state after registration, parsing, and chunking.
    Indexing moves a document through ``processing`` to ``indexed``,
    ``partial``, or ``failed``. ``completed`` remains available for later
    workflow steps and is not set by indexing.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    INDEXED = "indexed"
    PARTIAL = "partial"
    FAILED = "failed"


class OrmSchema(BaseModel):
    """Read schema that can be built from a SQLAlchemy instance."""

    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(BaseModel):
    """A serializable error. This is not a live exception."""

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    field: str | None = Field(default=None, max_length=128)


class PageParams(BaseModel):
    """Limit and offset for a list request."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PageMeta(BaseModel):
    """Pagination details returned with a list."""

    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
