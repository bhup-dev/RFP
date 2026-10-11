"""Addendum analysis unit tests use mocked retrieval and LLM responses."""

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.schemas.addendum import (
    AddendumAnalysisStatus,
    AddendumDocumentInfo,
    AddendumDocumentRole,
)
from app.schemas.citation import Citation
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.addendum_analysis_service import (
    AddendumAnalysisService,
    AddendumAnalysisUnavailable,
    AddendumBidNotFound,
    AddendumContext,
    AnalysisRunHandle,
    InvalidAddendumResponse,
    NoAddendaAvailable,
)


class Store:
    def __init__(self, context, chunks, citations):
        self.context = context
        self.chunks = chunks
        self.citations = {item.chunk_id: item for item in citations}
        self.started = []
        self.completed = []
        self.failed = []

    async def load_context(self, _bid_id):
        return self.context

    async def load_document_chunks(self, _bid_id, document_id, limit):
        return self.chunks.get(document_id, [])[:limit]

    async def start_run(self, _context, _model_name):
        handle = AnalysisRunHandle(uuid4(), datetime.now(UTC))
        self.started.append(handle)
        return handle

    async def resolve_citations(self, _bid_id, document_ids, chunk_ids):
        return {
            value: self.citations[value]
            for value in chunk_ids
            if value in self.citations
            and self.citations[value].document_id in document_ids
        }

    async def complete_run(self, run_id, findings, status, completed_at):
        self.completed.append((run_id, findings, status, completed_at))

    async def fail_run(self, run_id, detail, completed_at):
        self.failed.append((run_id, detail, completed_at))


class Retriever:
    def __init__(self, results):
        self.results = results
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return RetrievalResponse(
            query=request.query,
            bid_id=request.bid_id,
            results=self.results,
            total_results=len(self.results),
            retrieval_mode="hybrid",
        )


class LLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_response(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _chunk(bid_id, document_id, text, *, file_name, page=1):
    return RetrievedChunk(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=uuid4(),
        file_name=file_name,
        text=text,
        page_number=page,
        section_title="Requirements",
        chunk_type="text",
        keyword_score=1.0,
    )


def _citation(chunk):
    return Citation(
        bid_id=chunk.bid_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        excerpt=chunk.text,
    )


def _fixture(*, ordering_uncertain=False, relationship_uncertain=False):
    bid_id = uuid4()
    base_id = uuid4()
    addendum_id = uuid4()
    base = AddendumDocumentInfo(
        document_id=base_id,
        file_name="solicitation.pdf",
        role=AddendumDocumentRole.ORIGINAL,
        document_type="solicitation",
    )
    addendum = AddendumDocumentInfo(
        document_id=addendum_id,
        file_name="notice.pdf",
        role=AddendumDocumentRole.ADDENDUM,
        document_type="addendum",
        addendum_number=None if ordering_uncertain else 1,
        document_date=date(2027, 1, 2),
    )
    context = AddendumContext(
        bid_id=bid_id,
        original_documents=(base,),
        addendum_documents=(addendum,),
        relationship_uncertain=relationship_uncertain,
        ordering_uncertain=ordering_uncertain,
    )
    base_chunk = _chunk(
        bid_id,
        base_id,
        "Proposals are due May 1 at 2 PM ET. Quantity: 10 units.",
        file_name=base.file_name,
    )
    addendum_chunk = _chunk(
        bid_id,
        addendum_id,
        "The proposal deadline is revised to May 8 at 4 PM ET.",
        file_name=addendum.file_name,
    )
    store = Store(
        context,
        {addendum_id: [addendum_chunk]},
        [_citation(base_chunk), _citation(addendum_chunk)],
    )
    return context, base_chunk, addendum_chunk, store


def _topics(addendum_chunk, topic="Submission deadline", query="proposal deadline"):
    return json.dumps(
        {
            "topics": [
                {
                    "topic": topic,
                    "retrieval_query": query,
                    "addendum_chunk_ids": [str(addendum_chunk.chunk_id)],
                }
            ]
        }
    )


def _findings(
    classification,
    addendum_chunk,
    *,
    base_chunk=None,
    original_value=None,
    amended_value=None,
    topic="Submission deadline",
):
    return json.dumps(
        {
            "findings": [
                {
                    "topic": topic,
                    "classification": classification,
                    "original_value": original_value,
                    "amended_value": amended_value,
                    "explanation": "The addendum explicitly changes this requirement.",
                    "impact_note": "Use the cited amendment and review applicability.",
                    "severity": "high",
                    "confidence": 0.93,
                    "original_chunk_ids": (
                        [] if base_chunk is None else [str(base_chunk.chunk_id)]
                    ),
                    "addendum_chunk_ids": [str(addendum_chunk.chunk_id)],
                }
            ]
        }
    )


def _service(store, retriever, llm):
    return AddendumAnalysisService(
        store,
        retriever,
        llm,
        model_name="mock-model",
        max_chunks_per_document=10,
        related_top_k=5,
        max_evidence_chunks=20,
        max_output_tokens=4000,
    )


@pytest.mark.parametrize(
    ("classification", "original", "amended", "topic"),
    [
        ("modified_requirement", "May 1 at 2 PM ET", "May 8 at 4 PM ET", "Deadline"),
        ("modified_requirement", "10 units", "14 units", "Product quantity"),
        ("clarification", "Three-year warranty", "Warranty begins at acceptance", "Warranty"),
        ("removed_requirement", "Bid bond required", None, "Bid bond"),
        ("conflict", "Delivery in 30 days", "Delivery in 10 days", "Delivery"),
        ("ambiguous", "Model AB-1", "Model AB-I", "Model identifier"),
        ("no_change", "Net 30", "Net 30 remains unchanged", "Payment terms"),
    ],
)
def test_requirement_level_classifications_preserve_both_sides(
    classification, original, amended, topic
) -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    llm = LLM(
        _topics(addendum_chunk, topic),
        _findings(
            classification,
            addendum_chunk,
            base_chunk=base_chunk,
            original_value=original,
            amended_value=amended,
            topic=topic,
        ),
    )

    response = asyncio.run(
        _service(store, Retriever([base_chunk]), llm).analyze(context.bid_id)
    )

    finding = response.findings[0]
    assert finding.classification.value == classification
    assert finding.original_value == original
    assert finding.amended_value == amended
    assert finding.original_sources[0].chunk_id == base_chunk.chunk_id
    assert finding.addendum_sources[0].chunk_id == addendum_chunk.chunk_id
    assert finding.original_document_id == base_chunk.document_id
    assert finding.addendum_document_id == addendum_chunk.document_id


def test_new_mandatory_document_has_explicit_missing_original_side() -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    response = asyncio.run(
        _service(
            store,
            Retriever([base_chunk]),
            LLM(
                _topics(addendum_chunk, "Mandatory disclosure"),
                _findings(
                    "new_requirement",
                    addendum_chunk,
                    amended_value="Submit Form X with the proposal.",
                    topic="Mandatory disclosure",
                ),
            ),
        ).analyze(context.bid_id)
    )

    finding = response.findings[0]
    assert finding.original_value is None
    assert finding.original_sources == []
    assert finding.original_document_id is None
    assert finding.classification.value == "new_requirement"


def test_absent_original_reference_can_be_reported_as_ambiguous() -> None:
    context, base_chunk, addendum_chunk, store = _fixture(
        relationship_uncertain=True
    )
    response = asyncio.run(
        _service(
            store,
            Retriever([]),
            LLM(
                _topics(addendum_chunk, "Unresolved clause"),
                _findings(
                    "ambiguous",
                    addendum_chunk,
                    amended_value="Section 9 is revised, but Section 9 is unavailable.",
                    topic="Unresolved clause",
                ),
            ),
        ).analyze(context.bid_id)
    )

    assert response.findings[0].classification.value == "ambiguous"
    assert response.findings[0].original_sources == []
    assert response.relationship_uncertain is True
    assert any("relationship is uncertain" in value for value in response.warnings)


def test_omission_from_addendum_does_not_synthesize_removal() -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    llm = LLM(_topics(addendum_chunk), json.dumps({"findings": []}))

    response = asyncio.run(
        _service(store, Retriever([base_chunk]), llm).analyze(context.bid_id)
    )

    assert response.findings == []
    assert response.status == AddendumAnalysisStatus.COMPLETED
    assert "Absence from an addendum never removes" in llm.calls[1][1]["system"]


def test_invalid_and_cross_bid_citations_reject_findings() -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    foreign_chunk_id = uuid4()
    malformed = json.loads(
        _findings(
            "modified_requirement",
            addendum_chunk,
            base_chunk=base_chunk,
            original_value="May 1",
            amended_value="May 8",
        )
    )
    malformed["findings"][0]["original_chunk_ids"] = [str(foreign_chunk_id)]

    response = asyncio.run(
        _service(
            store,
            Retriever([base_chunk]),
            LLM(_topics(addendum_chunk), json.dumps(malformed)),
        ).analyze(context.bid_id)
    )

    assert response.status == AddendumAnalysisStatus.PARTIAL
    assert response.findings == []
    assert any("Rejected unsupported finding" in item for item in response.warnings)


def test_multiple_ordered_addenda_compare_second_against_prior_history() -> None:
    context, base_chunk, first_chunk, store = _fixture()
    second_info = AddendumDocumentInfo(
        document_id=uuid4(),
        file_name="addendum-2.pdf",
        role=AddendumDocumentRole.ADDENDUM,
        document_type="addendum",
        addendum_number=2,
    )
    second_chunk = _chunk(
        context.bid_id,
        second_info.document_id,
        "The deadline is revised again to May 10.",
        file_name=second_info.file_name,
    )
    store.context = AddendumContext(
        bid_id=context.bid_id,
        original_documents=context.original_documents,
        addendum_documents=(*context.addendum_documents, second_info),
        relationship_uncertain=False,
        ordering_uncertain=False,
    )
    store.chunks[second_info.document_id] = [second_chunk]
    store.citations[second_chunk.chunk_id] = _citation(second_chunk)
    retriever = Retriever([base_chunk, first_chunk])
    llm = LLM(
        _topics(first_chunk),
        _findings(
            "modified_requirement",
            first_chunk,
            base_chunk=base_chunk,
            original_value="May 1",
            amended_value="May 8",
        ),
        _topics(second_chunk),
        _findings(
            "modified_requirement",
            second_chunk,
            base_chunk=first_chunk,
            original_value="May 8",
            amended_value="May 10",
        ),
    )

    response = asyncio.run(
        _service(store, retriever, llm).analyze(context.bid_id)
    )

    assert len(response.findings) == 2
    assert first_chunk.document_id in retriever.requests[-1].document_ids


def test_uncertain_order_never_assumes_one_addendum_supersedes_another() -> None:
    context, base_chunk, first_chunk, store = _fixture(ordering_uncertain=True)
    second_info = AddendumDocumentInfo(
        document_id=uuid4(),
        file_name="clarification.pdf",
        role=AddendumDocumentRole.CLARIFICATION,
        document_type="clarification",
    )
    second_chunk = _chunk(
        context.bid_id,
        second_info.document_id,
        "A different deadline is stated.",
        file_name=second_info.file_name,
    )
    store.context = AddendumContext(
        bid_id=context.bid_id,
        original_documents=context.original_documents,
        addendum_documents=(context.addendum_documents[0], second_info),
        relationship_uncertain=False,
        ordering_uncertain=True,
    )
    store.chunks[second_info.document_id] = [second_chunk]
    store.citations[second_chunk.chunk_id] = _citation(second_chunk)
    retriever = Retriever([base_chunk, first_chunk])
    llm = LLM(
        _topics(first_chunk),
        json.dumps({"findings": []}),
        _topics(second_chunk),
        json.dumps({"findings": []}),
    )

    response = asyncio.run(
        _service(store, retriever, llm).analyze(context.bid_id)
    )

    assert response.ordering_uncertain is True
    assert all(
        request.document_ids == [context.original_documents[0].document_id]
        for request in retriever.requests
    )
    assert any("without assuming" in item for item in response.warnings)


def test_unknown_bid_and_bid_without_addenda_do_not_create_runs() -> None:
    missing = Store(None, {}, [])
    with pytest.raises(AddendumBidNotFound):
        asyncio.run(_service(missing, Retriever([]), LLM()).analyze(uuid4()))
    assert missing.started == []

    context, base_chunk, _addendum_chunk, store = _fixture()
    store.context = AddendumContext(
        bid_id=context.bid_id,
        original_documents=context.original_documents,
        addendum_documents=(),
        relationship_uncertain=False,
        ordering_uncertain=False,
    )
    with pytest.raises(NoAddendaAvailable):
        asyncio.run(
            _service(store, Retriever([base_chunk]), LLM()).analyze(context.bid_id)
        )
    assert store.started == []


@pytest.mark.parametrize(
    "comparison",
    ["not json", "[]", json.dumps({"findings": "none"}), json.dumps({"items": []})],
)
def test_malformed_model_output_fails_run(comparison) -> None:
    context, base_chunk, addendum_chunk, store = _fixture()

    with pytest.raises(InvalidAddendumResponse):
        asyncio.run(
            _service(
                store,
                Retriever([base_chunk]),
                LLM(_topics(addendum_chunk), comparison),
            ).analyze(context.bid_id)
        )

    assert len(store.failed) == 1
    assert store.completed == []


def test_invalid_findings_are_dropped_and_valid_findings_are_kept() -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    valid = json.loads(
        _findings(
            "modified_requirement",
            addendum_chunk,
            base_chunk=base_chunk,
            original_value="May 1",
            amended_value="May 8",
        )
    )["findings"][0]
    comparison = json.dumps(
        {
            "findings": [
                valid,
                {
                    **valid,
                    "topic": "Hot Swap Stock",
                    "classification": "clarification",
                    "original_chunk_ids": [],
                },
                {**valid, "topic": "Deadline", "classification": "superseded"},
                "not an object",
            ]
        }
    )

    response = asyncio.run(
        _service(
            store,
            Retriever([base_chunk]),
            LLM(_topics(addendum_chunk), comparison),
        ).analyze(context.bid_id)
    )

    assert response.status == AddendumAnalysisStatus.PARTIAL
    assert [item.topic for item in response.findings] == ["Submission deadline"]
    rejected = [item for item in response.warnings if "Rejected malformed finding" in item]
    assert len(rejected) == 3
    assert any("'Hot Swap Stock'" in item for item in rejected)
    assert any("(untitled)" in item for item in rejected)
    assert store.failed == []
    assert len(store.completed) == 1


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("provider")])
def test_provider_failures_are_safe_and_failed_run_is_persisted(failure) -> None:
    context, base_chunk, _addendum_chunk, store = _fixture()

    with pytest.raises(AddendumAnalysisUnavailable, match="unavailable"):
        asyncio.run(
            _service(store, Retriever([base_chunk]), LLM(failure)).analyze(
                context.bid_id
            )
        )

    assert store.failed[0][1] == "Addendum analysis provider or retrieval failed"


def test_repeated_analysis_preserves_distinct_runs() -> None:
    context, base_chunk, addendum_chunk, store = _fixture()
    llm = LLM(
        _topics(addendum_chunk),
        json.dumps({"findings": []}),
        _topics(addendum_chunk),
        json.dumps({"findings": []}),
    )
    service = _service(store, Retriever([base_chunk]), llm)

    first = asyncio.run(service.analyze(context.bid_id))
    second = asyncio.run(service.analyze(context.bid_id))

    assert first.run_id != second.run_id
    assert len(store.started) == 2
    assert len(store.completed) == 2
