"""Schema contract tests. No database, Qdrant, or model API is required."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models.bid import Bid
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.schemas import (
    ASSIGNMENT_FIELD_NAMES,
    AddendumChange,
    AddendumChangeStatus,
    AddendumReconciliationResult,
    AgentState,
    AgentStep,
    BidCreate,
    BidExtraction,
    BidRead,
    ChunkCreate,
    ChunkRead,
    Citation,
    DocumentCreate,
    DocumentRead,
    DocumentRecord,
    ExtractedField,
    FieldValidationStatus,
    IssueSeverity,
    IssueType,
    RetrievalRequest,
    RetrievedChunk,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
BID_ID = uuid4()
DOCUMENT_ID = uuid4()
CHUNK_ID = uuid4()


def _citation(*, page_number: int | None) -> Citation:
    return Citation(
        bid_id=BID_ID,
        document_id=DOCUMENT_ID,
        chunk_id=CHUNK_ID,
        file_name="addendum_1.pdf",
        page_number=page_number,
        section_title="Submission Requirements",
        excerpt="Bids are due on May 1.",
        relevance_score=0.8,
    )


def test_bid_create_accepts_separate_identifiers() -> None:
    bid = BidCreate(
        bid_identifier="Bid1",
        solicitation_number="SOL-100",
        title="School buses",
        issuing_organization="County",
    )
    assert bid.bid_identifier == "Bid1"
    assert bid.solicitation_number == "SOL-100"


@pytest.mark.parametrize(
    "payload",
    [
        {"bid_identifier": ""},
        {"bid_identifier": "x" * 129},
        {"bid_identifier": "Bid1", "solicitation_number": "s" * 256},
    ],
)
def test_bid_create_rejects_invalid_values(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        BidCreate.model_validate(payload)


def test_bid_read_parses_orm_attributes() -> None:
    orm = Bid(
        id=BID_ID,
        bid_identifier="Bid1",
        solicitation_number="SOL-100",
        title="School buses",
        issuing_organization="County",
        created_at=NOW,
        updated_at=NOW,
    )
    read = BidRead.model_validate(orm)
    dumped = read.model_dump(mode="json")
    assert dumped["id"] == str(BID_ID)
    assert dumped["bid_identifier"] == "Bid1"
    assert dumped["created_at"].startswith("2026-10-10T12:00:00")


def test_bid_read_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        BidRead(
            id=BID_ID,
            bid_identifier="Bid1",
            created_at=datetime(2026, 10, 10, 12, 0),
            updated_at=NOW,
        )


def test_document_validation_and_public_shape() -> None:
    created = DocumentCreate(
        bid_id=BID_ID,
        file_name="spec.pdf",
        storage_path="/data/spec.pdf",
        addendum_number=1,
    )
    assert created.ingestion_status.value == "pending"
    with pytest.raises(ValidationError):
        DocumentCreate(
            bid_id=BID_ID,
            file_name="",
            storage_path="/data/spec.pdf",
        )
    with pytest.raises(ValidationError):
        DocumentCreate(
            bid_id=BID_ID,
            file_name="spec.pdf",
            storage_path="/data/spec.pdf",
            addendum_number=-1,
        )

    orm = Document(
        id=DOCUMENT_ID,
        bid_id=BID_ID,
        file_name="spec.pdf",
        document_type="specification",
        file_hash="a" * 64,
        storage_path="/data/spec.pdf",
        document_date=None,
        addendum_number=1,
        ingestion_status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    public = DocumentRead.model_validate(orm)
    internal = DocumentRecord.model_validate(orm)
    assert "storage_path" not in public.model_dump()
    assert internal.storage_path == "/data/spec.pdf"


def test_chunk_validation_and_orm_metadata() -> None:
    chunk = ChunkCreate(
        document_id=DOCUMENT_ID,
        chunk_index=0,
        page_number=None,
        text="Visible text",
        metadata={"kind": "html"},
    )
    assert chunk.page_number is None
    with pytest.raises(ValidationError):
        ChunkCreate(document_id=DOCUMENT_ID, chunk_index=0, text="")
    with pytest.raises(ValidationError):
        ChunkCreate(document_id=DOCUMENT_ID, chunk_index=-1, text="Visible text")
    with pytest.raises(ValidationError):
        ChunkCreate(document_id=DOCUMENT_ID, chunk_index=0, page_number=0, text="Visible text")

    orm = DocumentChunk(
        id=CHUNK_ID,
        document_id=DOCUMENT_ID,
        chunk_index=2,
        page_number=4,
        section_title="Scope",
        chunk_type="text",
        text="Visible text",
        content_hash=None,
        chunk_metadata={"kind": "pdf"},
        created_at=NOW,
    )
    read = ChunkRead.model_validate(orm)
    assert read.metadata == {"kind": "pdf"}
    assert read.model_dump()["metadata"] == {"kind": "pdf"}
    from_json = ChunkRead.model_validate(
        {
            "id": CHUNK_ID,
            "document_id": DOCUMENT_ID,
            "chunk_index": 2,
            "text": "Visible text",
            "metadata": {"kind": "api"},
            "created_at": NOW,
        }
    )
    assert from_json.metadata == {"kind": "api"}


def test_citation_with_and_without_page_number() -> None:
    with_page = _citation(page_number=4).model_dump(mode="json")
    without_page = _citation(page_number=None).model_dump(mode="json")
    assert with_page["page_number"] == 4
    assert without_page["page_number"] is None
    assert without_page["chunk_id"] == str(CHUNK_ID)
    assert without_page["file_name"] == "addendum_1.pdf"
    with pytest.raises(ValidationError):
        _citation(page_number=0)


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_confidence_must_be_between_zero_and_one(score: float) -> None:
    with pytest.raises(ValidationError):
        ExtractedField(value="due May 1", confidence=score)


def test_confidence_bounds_are_accepted() -> None:
    assert ExtractedField(value="due May 1", confidence=0).confidence == 0
    assert ExtractedField(value="due May 1", confidence=1).confidence == 1


def test_bid_extraction_serializes_exact_field_names_and_nulls() -> None:
    dumped = BidExtraction().model_dump()
    assert list(dumped) == list(ASSIGNMENT_FIELD_NAMES)
    assert all(value is None for value in dumped.values())
    json_keys = list(BidExtraction().model_dump(mode="json"))
    assert json_keys == list(ASSIGNMENT_FIELD_NAMES)


def test_extracted_field_keeps_null_value_and_nested_citation() -> None:
    citation = _citation(page_number=None)
    field = ExtractedField(
        value=None,
        confidence=0.4,
        citations=[citation],
        notes="Not stated in the base bid.",
        validation_status=FieldValidationStatus.MISSING,
    )
    payload = BidExtraction(bid_number=field).model_dump(mode="json")
    stored = payload["Bid Number"]
    assert stored["value"] is None
    assert stored["citations"][0]["page_number"] is None
    assert stored["citations"][0]["excerpt"] == "Bids are due on May 1."
    assert payload["Title"] is None


def test_extracted_value_accepts_json_shapes_and_rejects_objects() -> None:
    ExtractedField(value={"lines": ["one", 2], "required": True})
    ExtractedField(value=["net 30", None])
    with pytest.raises(ValidationError):
        ExtractedField(value={"when": datetime.now(timezone.utc)})


def test_addendum_change_preserves_both_values_and_sources() -> None:
    original = _citation(page_number=2)
    amended = _citation(page_number=1)
    change = AddendumChange(
        field_name="Due Date",
        original_value="May 1, 2026",
        amended_value="May 15, 2026",
        status=AddendumChangeStatus.CONFLICTING,
        notes="Addendum 1 and the base bid disagree.",
        original_sources=[original],
        amended_sources=[amended],
    )
    result = AddendumReconciliationResult(bid_id=BID_ID, changes=[change])
    dumped = result.model_dump(mode="json")
    stored = dumped["changes"][0]
    assert stored["original_value"] == "May 1, 2026"
    assert stored["amended_value"] == "May 15, 2026"
    assert stored["status"] == "conflicting"
    assert stored["original_sources"][0]["page_number"] == 2
    assert stored["amended_sources"][0]["page_number"] == 1


def test_validation_issue_and_agent_state_round_trip() -> None:
    issue = ValidationIssue(
        field_name="Due Date",
        issue_type=IssueType.CONFLICTING_VALUES,
        message="Base bid and addendum name different due dates.",
        citations=[_citation(page_number=2)],
        severity=IssueSeverity.ERROR,
    )
    validation = ValidationResult(
        status=ValidationStatus.NEEDS_REVIEW,
        issues=[issue],
        metadata={"checked_fields": 20},
    )
    state = AgentState(
        bid_id=BID_ID,
        query="When is the bid due?",
        current_step=AgentStep.VALIDATE,
        retrieved_chunks=[
            RetrievedChunk(
                bid_id=BID_ID,
                document_id=DOCUMENT_ID,
                chunk_id=CHUNK_ID,
                file_name="addendum_1.pdf",
                text="The due date is May 15.",
                semantic_score=0.7,
                keyword_score=0.2,
                reranker_score=2.5,
            )
        ],
        extracted_fields=BidExtraction(),
        addendum_changes=[],
        validation_result=validation,
        errors=[],
    )
    restored = AgentState.model_validate_json(state.model_dump_json())
    assert restored.current_step is AgentStep.VALIDATE
    assert restored.validation_result is not None
    assert restored.validation_result.issues[0].citations[0].chunk_id == CHUNK_ID
    chunk = restored.retrieved_chunks[0]
    assert chunk.semantic_score == 0.7
    assert chunk.keyword_score == 0.2
    assert chunk.reranker_score == 2.5
    assert restored.extracted_fields is not None
    assert restored.extracted_fields.model_dump()["Due Date"] is None


def test_retrieval_request_bounds() -> None:
    request = RetrievalRequest(query="payment terms", bid_id=BID_ID, document_ids=[DOCUMENT_ID])
    assert request.limit == 8
    with pytest.raises(ValidationError):
        RetrievalRequest(query="", bid_id=BID_ID)
    with pytest.raises(ValidationError):
        RetrievalRequest(query="payment terms", limit=0)
    with pytest.raises(ValidationError):
        RetrievalRequest(query="payment terms", limit=51)


def test_namespace_without_storage_path_attribute_still_reads() -> None:
    """from_attributes reads only the public fields."""
    row = SimpleNamespace(
        id=DOCUMENT_ID,
        bid_id=BID_ID,
        file_name="notice.html",
        document_type="html",
        file_hash=None,
        document_date=None,
        addendum_number=None,
        ingestion_status="completed",
        created_at=NOW,
        updated_at=NOW,
    )
    read = DocumentRead.model_validate(row)
    assert read.file_name == "notice.html"
    assert read.ingestion_status.value == "completed"
