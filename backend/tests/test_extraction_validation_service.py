import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.validation import FieldValidationOutcome
from app.services.extraction_validation_service import (
    AddendumSnapshot,
    ExtractionRunBidMismatch,
    ExtractionSnapshot,
    ExtractionValidationService,
    StoredChange,
    StoredField,
    ValidationBidNotFound,
    ValidationRunHandle,
)


class Store:
    def __init__(self, snapshot, citations, addendum=None, bid_exists=True):
        self.snapshot = snapshot
        self.citations = {item.chunk_id: item for item in citations}
        self.addendum = addendum
        self.exists = bid_exists
        self.started = []
        self.completed = []
        self.failed = []

    async def bid_exists(self, _bid_id):
        return self.exists
    async def load_extraction(self, _run_id):
        return self.snapshot
    async def load_latest_addendum(self, _bid_id):
        return self.addendum
    async def resolve_chunks(self, ids):
        return {value: self.citations[value] for value in ids if value in self.citations}
    async def start_run(self, *_args):
        handle = ValidationRunHandle(uuid4(), datetime.now(UTC))
        self.started.append(handle)
        return handle
    async def complete_run(self, *args):
        self.completed.append(args)
    async def fail_run(self, *args):
        self.failed.append(args)


class LLM:
    def __init__(self, response=None):
        self.response = response
        self.calls = []
    async def generate_response(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _citation(bid_id, document_id, chunk_id, text):
    return Citation(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name="source.pdf",
        page_number=1,
        section_title="Requirements",
        excerpt=text,
    )


def _fixture(field_name="Bid Number", value="SOL-123", text="Solicitation number SOL-123"):
    bid_id, run_id, document_id, chunk_id = uuid4(), uuid4(), uuid4(), uuid4()
    citation = _citation(bid_id, document_id, chunk_id, text)
    fields = [
        StoredField(
            name,
            value if name == field_name else None,
            [citation.model_dump(mode="json")] if name == field_name else [],
        )
        for name in ASSIGNMENT_FIELD_NAMES
    ]
    return bid_id, run_id, citation, ExtractionSnapshot(run_id, bid_id, tuple(fields))


def _service(store, llm=None, **kwargs):
    return ExtractionValidationService(
        store,
        llm or LLM(),
        model_name="mock-model",
        semantic_enabled=kwargs.pop("semantic_enabled", False),
        **kwargs,
    )


def test_all_twenty_fields_and_exact_identifier_are_validated() -> None:
    bid_id, run_id, citation, snapshot = _fixture()
    store = Store(snapshot, [citation])
    report = asyncio.run(_service(store).validate(bid_id, run_id))
    assert tuple(report.fields) == ASSIGNMENT_FIELD_NAMES
    assert report.fields["Bid Number"].validation_status == FieldValidationOutcome.VALIDATED
    assert report.fields["Bid Number"].confidence_score == 1.0
    assert report.fields["Bid Number"].confidence_category.value == "high"
    assert report.summary.total_fields == 20
    assert report.summary.not_found == 19
    assert report.addendum_check_status.value == "not_run"
    assert report.warnings


def test_not_stated_field_is_a_verified_answer_without_review_or_model_call() -> None:
    bid_id, run_id, citation, snapshot = _fixture()
    fields = list(snapshot.fields)
    index = ASSIGNMENT_FIELD_NAMES.index("Bid Bond Requirement")
    fields[index] = StoredField(
        "Bid Bond Requirement",
        "Not specified in the bid documents",
        [citation.model_dump(mode="json")],
        "not_stated",
        "Only a generic reference to bond requirements noted elsewhere.",
    )
    llm = LLM(AssertionError("semantic check must not run for not_stated"))
    report = asyncio.run(
        _service(
            Store(ExtractionSnapshot(run_id, bid_id, tuple(fields)), [citation]),
            llm,
            semantic_enabled=True,
        ).validate(bid_id, run_id)
    )
    field = report.fields["Bid Bond Requirement"]
    assert field.validation_status == FieldValidationOutcome.NOT_STATED
    assert [item.chunk_id for item in field.supporting_citations] == [citation.chunk_id]
    assert field.validation_reasons == ["Only a generic reference to bond requirements noted elsewhere."]
    assert report.summary.not_stated == 1
    assert "Bid Bond Requirement" not in report.summary.fields_recommended_for_review
    assert not any("Bid Bond Requirement" in prompt for prompt, _ in llm.calls)


def test_invalid_cross_document_citation_never_receives_high_confidence() -> None:
    bid_id, run_id, citation, snapshot = _fixture()
    bad = citation.model_copy(update={"document_id": uuid4()})
    fields = list(snapshot.fields)
    fields[0] = StoredField("Bid Number", "SOL-123", [bad.model_dump(mode="json")])
    report = asyncio.run(
        _service(Store(ExtractionSnapshot(run_id, bid_id, tuple(fields)), [citation]))
        .validate(bid_id, run_id)
    )
    field = report.fields["Bid Number"]
    assert field.validation_status == FieldValidationOutcome.UNSUPPORTED
    assert field.confidence_score == 0
    assert field.confidence_category.value == "low"


@pytest.mark.parametrize(
    ("field_name", "value", "text"),
    [
        ("Model_no", "WD-22TB4", "Required model WD-22TB4."),
        ("Part_no", "JA-207652", "Part number JA-207652."),
        ("Due Date", "June 5, 2027 at 2 PM ET", "Due June 5, 2027 at 2 PM ET."),
        ("Product", {"quantity": "14 units"}, "Product quantity is 14 units."),
    ],
)
def test_identifiers_dates_quantities_and_units_are_preserved(field_name, value, text) -> None:
    bid_id, run_id, citation, snapshot = _fixture(field_name, value, text)
    report = asyncio.run(_service(Store(snapshot, [citation])).validate(bid_id, run_id))
    assert report.fields[field_name].extracted_value == value
    assert report.fields[field_name].validation_status == FieldValidationOutcome.VALIDATED


def test_semantic_verifier_can_mark_summary_partially_supported() -> None:
    bid_id, run_id, citation, snapshot = _fixture(
        "Bid Summary",
        "Delivery is required in 30 days and includes a five-year warranty.",
        "Delivery is required in 30 days.",
    )
    verdict = json.dumps(
        {
            "status": "partially_supported",
            "directness": 0.8,
            "completeness": 0.5,
            "supporting_chunk_ids": [str(citation.chunk_id)],
            "contradicting_chunk_ids": [],
            "reasons": ["Delivery is supported; the warranty is not."],
            "recommended_action": "Remove or verify the warranty claim.",
        }
    )
    report = asyncio.run(
        _service(Store(snapshot, [citation]), LLM(verdict), semantic_enabled=True)
        .validate(bid_id, run_id)
    )
    assert report.fields["Bid Summary"].validation_status == FieldValidationOutcome.PARTIALLY_SUPPORTED
    assert report.fields["Bid Summary"].confidence_score < 0.8


def test_required_versus_optional_document_uses_semantic_evidence() -> None:
    bid_id, run_id, citation, snapshot = _fixture(
        "Additional Documentation Required",
        ["Form X"],
        "Form X is informational and optional.",
    )
    verdict = json.dumps(
        {
            "status": "unsupported",
            "directness": 0.2,
            "completeness": 0.0,
            "supporting_chunk_ids": [],
            "contradicting_chunk_ids": [str(citation.chunk_id)],
            "reasons": ["The cited form is optional, not mandatory."],
            "recommended_action": "Do not present Form X as required.",
        }
    )
    report = asyncio.run(
        _service(Store(snapshot, [citation]), LLM(verdict), semantic_enabled=True)
        .validate(bid_id, run_id)
    )
    assert report.fields["Additional Documentation Required"].validation_status == FieldValidationOutcome.UNSUPPORTED


def test_relevant_addendum_conflict_overrides_high_literal_support() -> None:
    bid_id, run_id, original, snapshot = _fixture(
        "Due Date", "May 1 at 2 PM ET", "Due May 1 at 2 PM ET."
    )
    addendum_citation = _citation(
        bid_id, uuid4(), uuid4(), "Addendum changes the deadline to May 8 at 4 PM ET."
    )
    sources = [
        {"side": "original", **original.model_dump(mode="json")},
        {"side": "addendum", **addendum_citation.model_dump(mode="json")},
    ]
    addendum = AddendumSnapshot(
        uuid4(),
        (
            StoredChange(
                "Due deadline",
                "conflict",
                original.document_id,
                addendum_citation.document_id,
                "May 1 at 2 PM ET",
                "May 8 at 4 PM ET",
                "high",
                sources,
            ),
        ),
    )
    report = asyncio.run(
        _service(Store(snapshot, [original, addendum_citation], addendum=addendum))
        .validate(bid_id, run_id)
    )
    field = report.fields["Due Date"]
    assert field.validation_status == FieldValidationOutcome.CONFLICTING
    assert field.confidence_score <= 0.35
    assert len(field.contradicting_citations) == 2
    assert report.overall_status.value == "blocked"


@pytest.mark.parametrize("response", ["not json", TimeoutError()])
def test_semantic_failure_requires_review_and_is_not_high_confidence(response) -> None:
    bid_id, run_id, citation, snapshot = _fixture(
        "Bid Summary", "A synthesized summary.", "Source evidence."
    )
    report = asyncio.run(
        _service(Store(snapshot, [citation]), LLM(response), semantic_enabled=True)
        .validate(bid_id, run_id)
    )
    field = report.fields["Bid Summary"]
    assert field.validation_status == FieldValidationOutcome.NEEDS_REVIEW
    assert field.confidence_category.value == "low"


def test_thresholds_are_configurable_and_invalid_order_is_rejected() -> None:
    with pytest.raises(ValueError):
        _service(Store(None, []), high_threshold=0.5, medium_threshold=0.7)


def test_unknown_bid_and_cross_bid_run_are_rejected_before_history() -> None:
    with pytest.raises(ValidationBidNotFound):
        asyncio.run(_service(Store(None, [], bid_exists=False)).validate(uuid4(), uuid4()))
    bid_id, run_id, citation, snapshot = _fixture()
    with pytest.raises(ExtractionRunBidMismatch):
        asyncio.run(
            _service(Store(snapshot, [citation])).validate(uuid4(), run_id)
        )


def test_repeated_validation_preserves_distinct_runs() -> None:
    bid_id, run_id, citation, snapshot = _fixture()
    store = Store(snapshot, [citation])
    service = _service(store)
    first = asyncio.run(service.validate(bid_id, run_id))
    second = asyncio.run(service.validate(bid_id, run_id))
    assert first.validation_run_id != second.validation_run_id
    assert len(store.completed) == 2
