import asyncio
from uuid import uuid4

import pytest

from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.services.bid_intelligence_service import (
    AddendumFindingSource,
    BidIntelligenceService,
    BidSource,
    ExtractionFieldSource,
    ExtractionSource,
    IntelligenceBidNotFound,
    IntelligenceExtractionBidMismatch,
    ValidationFieldSource,
    ValidationSource,
)


class Store:
    def __init__(self, bid, extraction=None, validation=None, addendum=(None, ()), citations=()):
        self.bid = bid
        self.extraction = extraction
        self.validation = validation
        self.addendum = addendum
        self.citations = {item.chunk_id: item for item in citations}
        self.exists = extraction is not None
    async def load_bid(self, _bid_id):
        return self.bid
    async def load_extraction(self, _bid_id, _run_id):
        return self.extraction
    async def extraction_exists(self, _run_id):
        return self.exists
    async def load_validation(self, _run_id):
        return self.validation
    async def load_addendum(self, _bid_id, _run_id):
        return self.addendum
    async def resolve_chunks(self, ids):
        return {value: self.citations[value] for value in ids if value in self.citations}


def _fixture(*, validation=True, explicit_addenda=False):
    bid_id, document_id, chunk_id, extraction_id = uuid4(), uuid4(), uuid4(), uuid4()
    citation = Citation(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name="source.pdf",
        page_number=1,
        section_title="Header",
        excerpt="Solicitation number SOL-1.",
    )
    extraction_fields = tuple(
        ExtractionFieldSource(
            name,
            "SOL-1" if name == "Bid Number" else None,
            "Extracted from the header." if name == "Bid Number" else None,
            [citation.model_dump(mode="json")] if name == "Bid Number" else [],
        )
        for name in ASSIGNMENT_FIELD_NAMES
    )
    extraction = ExtractionSource(extraction_id, bid_id, (document_id,), extraction_fields)
    validation_source = None
    if validation:
        validation_id = uuid4()
        validation_source = ValidationSource(
            validation_id,
            extraction_id,
            None,
            tuple(
                ValidationFieldSource(
                    name,
                    "SOL-1" if name == "Bid Number" else None,
                    "validated" if name == "Bid Number" else "not_found",
                    1.0 if name == "Bid Number" else None,
                    [citation.model_dump(mode="json")] if name == "Bid Number" else [],
                    [],
                    "Routine use." if name == "Bid Number" else "Inspect if required.",
                )
                for name in ASSIGNMENT_FIELD_NAMES
            ),
        )
    bid = BidSource(bid_id, "synthetic-bid", "SOL-1", (document_id,), explicit_addenda)
    return bid, extraction, validation_source, citation


def test_consolidates_all_exact_fields_and_serializes_validated_values():
    bid, extraction, validation, citation = _fixture()
    profile = asyncio.run(
        BidIntelligenceService(Store(bid, extraction, validation, citations=[citation]))
        .build(bid.bid_id)
    )
    dumped = profile.model_dump(mode="json")
    assert tuple(dumped["fields"]) == ASSIGNMENT_FIELD_NAMES
    assert dumped["fields"]["Bid Number"]["value"] == "SOL-1"
    assert dumped["fields"]["Bid Number"]["validation_status"] == "validated"
    assert dumped["fields"]["Bid Number"]["supporting_citations"][0]["chunk_id"] == str(citation.chunk_id)
    assert dumped["metadata"]["status"] == "needs_review"


def test_missing_validation_is_unvalidated_and_profile_incomplete():
    bid, extraction, _validation, citation = _fixture(validation=False)
    profile = asyncio.run(
        BidIntelligenceService(Store(bid, extraction, citations=[citation])).build(bid.bid_id)
    )
    assert profile.metadata.status.value == "incomplete"
    assert profile.fields["Bid Number"].validation_status.value == "unvalidated"
    assert profile.fields["Bid Number"].confidence_score is None


def test_missing_extraction_returns_an_incomplete_twenty_field_profile():
    bid, _extraction, _validation, _citation = _fixture()
    profile = asyncio.run(BidIntelligenceService(Store(bid)).build(bid.bid_id))
    assert profile.metadata.status.value == "incomplete"
    assert tuple(profile.fields) == ASSIGNMENT_FIELD_NAMES
    assert all(item.value is None for item in profile.fields.values())


def test_no_addenda_is_not_failure_but_missing_required_analysis_is_incomplete():
    bid, extraction, validation, citation = _fixture()
    no_addenda = asyncio.run(
        BidIntelligenceService(Store(bid, extraction, validation, citations=[citation]))
        .build(bid.bid_id)
    )
    assert no_addenda.metadata.status.value != "failed"

    explicit = BidSource(
        bid.bid_id, bid.bid_identifier, bid.solicitation_number, bid.document_ids, True
    )
    missing = asyncio.run(
        BidIntelligenceService(Store(explicit, extraction, validation, citations=[citation]))
        .build(bid.bid_id)
    )
    assert missing.metadata.status.value == "incomplete"
    assert any("addendum" in item.lower() for item in missing.metadata.warnings)


def test_conflicting_due_date_preserves_original_amended_and_both_citations():
    bid, extraction, validation, citation = _fixture()
    addendum_citation = citation.model_copy(
        update={
            "document_id": uuid4(),
            "chunk_id": uuid4(),
            "file_name": "addendum.pdf",
            "excerpt": "Deadline changed to May 8.",
        }
    )
    addendum_run = uuid4()
    finding = AddendumFindingSource(
        uuid4(),
        addendum_run,
        "Due deadline",
        "conflict",
        "May 1",
        "May 8",
        "The deadlines conflict.",
        "high",
        citation.document_id,
        addendum_citation.document_id,
        [
            {"side": "original", **citation.model_dump(mode="json")},
            {"side": "addendum", **addendum_citation.model_dump(mode="json")},
        ],
    )
    fields = list(validation.fields)
    due_index = ASSIGNMENT_FIELD_NAMES.index("Due Date")
    extraction_fields = list(extraction.fields)
    extraction_fields[due_index] = ExtractionFieldSource(
        "Due Date", "May 1", "Original deadline.", [citation.model_dump(mode="json")]
    )
    extraction = ExtractionSource(
        extraction.run_id,
        extraction.bid_id,
        extraction.document_ids,
        tuple(extraction_fields),
    )
    fields[due_index] = ValidationFieldSource(
        "Due Date",
        "May 1",
        "conflicting",
        0.3,
        [citation.model_dump(mode="json")],
        [addendum_citation.model_dump(mode="json")],
        "Resolve the conflict.",
    )
    validation = ValidationSource(
        validation.run_id, extraction.run_id, addendum_run, tuple(fields)
    )
    profile = asyncio.run(
        BidIntelligenceService(
            Store(
                bid,
                extraction,
                validation,
                (addendum_run, (finding,)),
                [citation, addendum_citation],
            )
        ).build(bid.bid_id)
    )
    due = profile.fields["Due Date"]
    assert due.validation_status.value == "conflicting"
    assert due.addendum_changes[0].original_value == "May 1"
    assert due.addendum_changes[0].amended_value == "May 8"
    assert due.addendum_changes[0].original_sources
    assert due.addendum_changes[0].addendum_sources
    assert profile.metadata.status.value == "needs_review"


def test_partially_supported_and_unsupported_statuses_are_not_upgraded():
    bid, extraction, validation, citation = _fixture()
    rows = list(validation.fields)
    rows[ASSIGNMENT_FIELD_NAMES.index("Title")] = ValidationFieldSource(
        "Title", None, "partially_supported", 0.6, [], [], "Review title."
    )
    rows[ASSIGNMENT_FIELD_NAMES.index("Product")] = ValidationFieldSource(
        "Product", None, "unsupported", 0.1, [], [], "Do not use."
    )
    validation = ValidationSource(
        validation.run_id, extraction.run_id, None, tuple(rows)
    )
    profile = asyncio.run(
        BidIntelligenceService(Store(bid, extraction, validation, citations=[citation]))
        .build(bid.bid_id)
    )
    assert profile.fields["Title"].validation_status.value == "partially_supported"
    assert profile.fields["Product"].validation_status.value == "unsupported"
    assert profile.metadata.status.value == "needs_review"


def test_multiple_addendum_findings_are_preserved_individually():
    bid, extraction, validation, citation = _fixture()
    addendum_run = uuid4()
    addendum_document = uuid4()
    addendum_citation = citation.model_copy(
        update={
            "document_id": addendum_document,
            "chunk_id": uuid4(),
            "file_name": "addenda.pdf",
            "excerpt": "Deadline and term are revised.",
        }
    )
    findings = tuple(
        AddendumFindingSource(
            uuid4(),
            addendum_run,
            topic,
            "modified_requirement",
            original,
            amended,
            "Explicit revision.",
            "medium",
            citation.document_id,
            addendum_document,
            [
                {"side": "original", **citation.model_dump(mode="json")},
                {"side": "addendum", **addendum_citation.model_dump(mode="json")},
            ],
        )
        for topic, original, amended in (
            ("Due deadline", "May 1", "May 8"),
            ("Term duration", "One year", "Two years"),
        )
    )
    validation = ValidationSource(
        validation.run_id,
        validation.extraction_run_id,
        addendum_run,
        validation.fields,
    )
    profile = asyncio.run(
        BidIntelligenceService(
            Store(
                bid,
                extraction,
                validation,
                (addendum_run, findings),
                [citation, addendum_citation],
            )
        ).build(bid.bid_id)
    )
    assert len(profile.fields["Due Date"].addendum_changes) == 1
    assert len(profile.fields["Term of Bid"].addendum_changes) == 1


def test_invalid_reference_produces_warning_not_fabricated_citation():
    bid, extraction, validation, _citation = _fixture()
    profile = asyncio.run(
        BidIntelligenceService(Store(bid, extraction, validation)).build(bid.bid_id)
    )
    assert profile.fields["Bid Number"].supporting_citations == []
    assert any("invalid" in item for item in profile.metadata.warnings)


def test_unknown_bid_and_foreign_selected_run_are_rejected():
    with pytest.raises(IntelligenceBidNotFound):
        asyncio.run(BidIntelligenceService(Store(None)).build(uuid4()))
    bid, _extraction, _validation, _citation = _fixture()
    store = Store(bid)
    store.exists = True
    with pytest.raises(IntelligenceExtractionBidMismatch):
        asyncio.run(BidIntelligenceService(store).build(bid.bid_id, uuid4()))


def test_repeat_generation_is_equivalent_except_generation_time():
    bid, extraction, validation, citation = _fixture()
    service = BidIntelligenceService(Store(bid, extraction, validation, citations=[citation]))
    first = asyncio.run(service.build(bid.bid_id))
    second = asyncio.run(service.build(bid.bid_id))
    assert first.metadata.profile_id == second.metadata.profile_id
    assert first.fields == second.fields
    assert first.metadata.extraction_run_id == second.metadata.extraction_run_id
