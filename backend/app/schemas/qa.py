"""Request and response models for grounded bid Q&A."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.retrieval import RetrievalMode


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    compare_bid_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("question")
    @classmethod
    def question_has_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("question must not be blank")
        return text

    @field_validator("compare_bid_ids")
    @classmethod
    def compare_bids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class AnswerSource(BaseModel):
    """One cited item: a document passage, a stored bid record, or a system policy note.

    Records without a source passage and policy notes have no chunk or document.
    """

    source_id: str
    kind: Literal["passage", "record", "policy"] = "passage"
    bid_id: UUID | None = None
    bid_label: str | None = None
    chunk_id: UUID | None = None
    document_id: UUID | None = None
    file_name: str | None = None
    page_number: int | None = None
    section_title: str | None = None
    excerpt: str
    record: str | None = None


class AskResponse(BaseModel):
    bid_id: UUID
    question: str
    answer: str
    found: bool
    sources: list[AnswerSource] = Field(default_factory=list)
    retrieval_mode: RetrievalMode | None = None
    warnings: list[str] = Field(default_factory=list)
    model: str | None = None


class LLMAnswerPayload(BaseModel):
    """Structured output the model must return."""

    answer: str
    found: bool
    cited_sources: list[str] = Field(default_factory=list)
