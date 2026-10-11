"""Structured extraction tests use fakes and never call external providers."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas.citation import Citation
from app.schemas.extraction import (
    ASSIGNMENT_FIELD_NAMES,
    NOT_STATED_ANSWER,
    ExtractionRunStatus,
    FieldValidationStatus,
)
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.extraction_service import (
    FIELD_ABSENCE_TERMS,
    FIELD_GROUPS,
    BidNotFound,
    BidNotReady,
    EligibleBid,
    ExtractionService,
    ExtractionUnavailable,
    InvalidExtractionResponse,
    RunHandle,
)


class Store:
    def __init__(
        self,
        context: EligibleBid | None,
        citations: list[Citation] = (),
        *,
        searched_chunks: int = 0,
        text_hits: dict[str, list[RetrievedChunk]] | None = None,
    ) -> None:
        self.context = context
        self.citations = {item.chunk_id: item for item in citations}
        self.searched_chunks = searched_chunks
        self.text_hits = text_hits or {}
        self.text_searches = []
        self.started: list[RunHandle] = []
        self.completed = []
        self.failed = []

    async def search_chunk_text(self, _bid_id, _document_ids, pattern, limit):
        self.text_searches.append(pattern)
        field = next(name for name, terms in FIELD_ABSENCE_TERMS.items() if terms == pattern)
        return self.text_hits.get(field, [])[:limit], self.searched_chunks

    async def load_eligible_bid(self, _bid_id):
        return self.context

    async def start_run(self, _bid_id, _document_ids, _model_name):
        handle = RunHandle(uuid4(), datetime.now(UTC))
        self.started.append(handle)
        return handle

    async def resolve_citations(self, _bid_id, _document_ids, chunk_ids):
        return {value: self.citations[value] for value in chunk_ids if value in self.citations}

    async def complete_run(self, run_id, fields, status, completed_at):
        self.completed.append((run_id, fields, status, completed_at))

    async def fail_run(self, run_id, detail, completed_at):
        self.failed.append((run_id, detail, completed_at))


class Retriever:
    def __init__(self, results: list[RetrievedChunk]) -> None:
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
    def __init__(self, response: str | BaseException) -> None:
        self.response = response
        self.calls = []

    async def generate_response(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _claim(
    *,
    value=None,
    confidence=None,
    chunk_ids=(),
    notes=None,
    status="not_found",
):
    return {
        "value": value,
        "confidence": confidence,
        "chunk_ids": [str(item) for item in chunk_ids],
        "notes": notes,
        "status": status,
    }


def _payload(**overrides):
    payload = {name: _claim() for name in ASSIGNMENT_FIELD_NAMES}
    payload.update(overrides)
    return json.dumps(payload)


def _fixture():
    bid_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    context = EligibleBid(bid_id, (document_id,))
    result = RetrievedChunk(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name="requirements.pdf",
        text=(
            "Solicitation JA-207652 closes June 5, 2027 at 2:00 PM ET. "
            "Model WD22TB4 is required. A bid bond is not required."
        ),
        page_number=3,
        section_title="Requirements",
        chunk_type="text",
        semantic_score=0.9,
    )
    citation = Citation(
        bid_id=bid_id,
        document_id=document_id,
        chunk_id=chunk_id,
        file_name=result.file_name,
        page_number=result.page_number,
        section_title=result.section_title,
        excerpt=result.text,
    )
    return context, result, citation


def _service(store, retriever, llm):
    return ExtractionService(
        store,
        retriever,
        llm,
        model_name="mock-model",
        query_top_k=7,
        max_evidence_chunks=60,
        max_output_tokens=4000,
    )


def test_extracts_exact_fields_preserves_identifiers_dates_and_explicit_negative() -> None:
    context, result, citation = _fixture()
    response = _payload(
        **{
            "Bid Number": _claim(
                value="JA-207652",
                confidence=0.99,
                chunk_ids=(result.chunk_id,),
                status="extracted",
            ),
            "Due Date": _claim(
                value="June 5, 2027 at 2:00 PM ET",
                confidence=0.95,
                chunk_ids=(result.chunk_id,),
                status="extracted",
            ),
            "Bid Bond Requirement": _claim(
                value=False,
                confidence=0.98,
                chunk_ids=(result.chunk_id,),
                notes="The source explicitly says a bond is not required.",
                status="extracted",
            ),
            "Model_no": _claim(
                value="WD22TB4",
                confidence=0.97,
                chunk_ids=(result.chunk_id,),
                status="extracted",
            ),
        }
    )
    store = Store(context, [citation])
    retriever = Retriever([result])
    llm = LLM(response)

    output = asyncio.run(_service(store, retriever, llm).extract(context.id))

    serialized = output.fields.model_dump(by_alias=True, mode="json")
    assert tuple(serialized) == ASSIGNMENT_FIELD_NAMES
    assert serialized["Bid Number"]["value"] == "JA-207652"
    assert serialized["Due Date"]["value"] == "June 5, 2027 at 2:00 PM ET"
    assert serialized["Model_no"]["value"] == "WD22TB4"
    assert serialized["Bid Bond Requirement"]["value"] is False
    assert serialized["Bid Bond Requirement"]["validation_status"] == "supported"
    assert serialized["Bid Bond Requirement"]["citations"][0]["chunk_id"] == str(result.chunk_id)
    assert serialized["Payment Terms"]["value"] is None
    assert serialized["Payment Terms"]["validation_status"] == "missing"
    assert output.status == ExtractionRunStatus.COMPLETED
    assert len(retriever.requests) == sum(len(group.queries) for group in FIELD_GROUPS)
    assert all(request.bid_id == context.id for request in retriever.requests)
    assert all(request.document_ids == list(context.document_ids) for request in retriever.requests)
    assert all(request.top_k == 7 for request in retriever.requests)
    assert len(llm.calls) == len(FIELD_GROUPS)
    assert all("sk-" not in prompt for prompt, _ in llm.calls)
    assert all("untrusted document content" in kwargs["system"] for _, kwargs in llm.calls)


def test_field_groups_cover_every_field_once_and_prompts_request_only_their_group() -> None:
    grouped = [name for group in FIELD_GROUPS for name in group.fields]
    assert sorted(grouped) == sorted(ASSIGNMENT_FIELD_NAMES)
    assert len(grouped) == len(set(grouped))

    context, result, citation = _fixture()
    llm = LLM(_payload())
    asyncio.run(_service(Store(context, [citation]), Retriever([result]), llm).extract(context.id))

    prompts = [prompt for prompt, _ in llm.calls]
    for group in FIELD_GROUPS:
        requested = json.dumps(list(group.fields), ensure_ascii=False)
        assert sum(requested in prompt for prompt in prompts) == 1


def test_invalid_or_cross_bid_citation_rejects_only_that_field() -> None:
    context, result, citation = _fixture()
    foreign_chunk = uuid4()
    store = Store(context, [citation])
    llm = LLM(
        _payload(
            **{
                "Title": _claim(
                    value="Unsupported title",
                    confidence=0.8,
                    chunk_ids=(foreign_chunk,),
                    status="extracted",
                ),
                "Bid Number": _claim(
                    value="JA-207652",
                    confidence=0.9,
                    chunk_ids=(result.chunk_id,),
                    status="extracted",
                ),
            }
        )
    )

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    assert output.status == ExtractionRunStatus.PARTIAL
    assert output.fields.title is not None
    assert output.fields.title.value is None
    assert output.fields.title.citations == []
    assert output.fields.title.validation_status == FieldValidationStatus.UNSUPPORTED
    assert "Rejected 1 citation" in (output.fields.title.notes or "")
    assert output.fields.bid_number is not None
    assert output.fields.bid_number.validation_status == FieldValidationStatus.SUPPORTED


def test_conflicting_evidence_is_retained_with_both_authoritative_citations() -> None:
    context, first, first_citation = _fixture()
    second_id = uuid4()
    second = first.model_copy(
        update={
            "chunk_id": second_id,
            "text": "Addendum: proposals now close June 7, 2027 at 4:00 PM ET.",
            "page_number": 1,
            "file_name": "addendum.pdf",
        }
    )
    second_citation = first_citation.model_copy(
        update={
            "chunk_id": second_id,
            "excerpt": second.text,
            "page_number": 1,
            "file_name": "addendum.pdf",
        }
    )
    llm = LLM(
        _payload(
            **{
                "Due Date": _claim(
                    value=[
                        "June 5, 2027 at 2:00 PM ET",
                        "June 7, 2027 at 4:00 PM ET",
                    ],
                    confidence=0.7,
                    chunk_ids=(first.chunk_id, second.chunk_id),
                    notes="The original and addendum state different deadlines.",
                    status="conflicting",
                )
            }
        )
    )
    store = Store(context, [first_citation, second_citation])

    output = asyncio.run(
        _service(store, Retriever([first, second]), llm).extract(context.id)
    )

    assert output.fields.due_date is not None
    assert output.fields.due_date.validation_status == FieldValidationStatus.CONFLICTING
    assert len(output.fields.due_date.citations) == 2
    assert output.status == ExtractionRunStatus.COMPLETED


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"Bid Number": _claim()}),
    ],
)
def test_malformed_provider_output_fails_and_records_failed_run(raw: str) -> None:
    context, result, citation = _fixture()
    store = Store(context, [citation])

    with pytest.raises(InvalidExtractionResponse, match="malformed"):
        asyncio.run(_service(store, Retriever([result]), LLM(raw)).extract(context.id))

    assert len(store.started) == 1
    assert len(store.failed) == 1
    assert store.completed == []


def test_invalid_field_claim_rejects_only_that_field() -> None:
    context, result, citation = _fixture()
    store = Store(context, [citation])
    raw = _payload(
        Title={"value": "x", "status": "extracted"},
        **{
            "Bid Number": _claim(
                value="JA-207652",
                confidence=0.9,
                chunk_ids=(result.chunk_id,),
                status="extracted",
            )
        },
    )

    output = asyncio.run(_service(store, Retriever([result]), LLM(raw)).extract(context.id))

    assert output.status == ExtractionRunStatus.PARTIAL
    assert output.fields.title.value is None
    assert output.fields.title.validation_status == FieldValidationStatus.UNSUPPORTED
    assert "extraction contract" in output.fields.title.notes
    assert output.fields.bid_number.validation_status == FieldValidationStatus.SUPPORTED
    assert len(store.completed) == 1
    assert store.failed == []


def test_contract_breaking_group_is_retried_once_with_the_errors() -> None:
    context, result, citation = _fixture()
    fixed_title = _claim(
        value="Student and Staff Computing Devices",
        confidence=0.9,
        chunk_ids=(result.chunk_id,),
        status="extracted",
    )

    class RepairingLLM(LLM):
        async def generate_response(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            if "broke the output contract" in prompt:
                return _payload(Title=fixed_title)
            return _payload(Title={"value": "x", "status": "extracted"})

    llm = RepairingLLM("")
    output = asyncio.run(
        _service(Store(context, [citation]), Retriever([result]), llm).extract(context.id)
    )

    repairs = [prompt for prompt, _ in llm.calls if "broke the output contract" in prompt]
    assert len(repairs) == 1
    assert '"Title"' in repairs[0]
    assert output.fields.title.value == "Student and Staff Computing Devices"
    assert output.fields.title.validation_status == FieldValidationStatus.SUPPORTED


def test_unknown_and_not_ready_bids_do_not_create_runs() -> None:
    missing = Store(None)
    with pytest.raises(BidNotFound):
        asyncio.run(_service(missing, Retriever([]), LLM("{}")).extract(uuid4()))
    assert missing.started == []

    bid_id = uuid4()
    not_ready = Store(EligibleBid(bid_id, ()))
    with pytest.raises(BidNotReady):
        asyncio.run(_service(not_ready, Retriever([]), LLM("{}")).extract(bid_id))
    assert not_ready.started == []


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("provider failed")])
def test_provider_failures_are_safe_and_persist_failed_status(failure: BaseException) -> None:
    context, result, citation = _fixture()
    store = Store(context, [citation])

    with pytest.raises(ExtractionUnavailable, match="unavailable"):
        asyncio.run(
            _service(store, Retriever([result]), LLM(failure)).extract(context.id)
        )

    assert len(store.failed) == 1
    assert store.failed[0][1] == "Extraction provider or retrieval failed"


def test_repeat_extraction_creates_distinct_historical_runs() -> None:
    context, result, citation = _fixture()
    store = Store(context, [citation])
    llm = LLM(_payload())
    service = _service(store, Retriever([result]), llm)

    first = asyncio.run(service.extract(context.id))
    second = asyncio.run(service.extract(context.id))

    assert first.run_id != second.run_id
    assert len(store.started) == 2
    assert len(store.completed) == 2


def test_no_retrieved_evidence_persists_twenty_missing_fields_without_llm() -> None:
    context, _result, _citation = _fixture()
    store = Store(context)
    llm = LLM(AssertionError("LLM must not be called"))

    output = asyncio.run(_service(store, Retriever([]), llm).extract(context.id))

    fields = output.fields.model_dump(by_alias=True, mode="json")
    assert len(fields) == 20
    assert all(item["value"] is None for item in fields.values())
    assert all(item["validation_status"] == "missing" for item in fields.values())
    assert llm.calls == []


class GapLLM(LLM):
    """Answers grouped prompts with all fields not found and gap prompts with ``gap_claim``."""

    def __init__(self, gap_claim: dict) -> None:
        super().__init__("")
        self.gap_claim = gap_claim

    async def generate_response(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if "earlier extraction did not find" in prompt:
            return json.dumps({"MFG for Registration": self.gap_claim})
        return _payload()


def _gap_prompts(llm: LLM) -> list[str]:
    return [prompt for prompt, _ in llm.calls if "earlier extraction did not find" in prompt]


def test_gap_check_without_matching_text_answers_not_stated_without_model_call() -> None:
    context, result, citation = _fixture()
    store = Store(context, [citation], searched_chunks=42)
    llm = GapLLM(_claim())

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    assert _gap_prompts(llm) == []
    assert len(store.text_searches) == 20
    field = output.fields.bid_bond_requirement
    assert field.value == NOT_STATED_ANSWER
    assert field.validation_status == FieldValidationStatus.NOT_STATED
    assert field.citations == []
    assert "42 chunks" in field.notes
    assert output.status == ExtractionRunStatus.COMPLETED


def test_gap_check_recovers_a_stated_value_from_matching_text() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(
        update={"chunk_id": uuid4(), "text": "The Master Contractor must be an authorized reseller for Dell."}
    )
    hit_citation = citation.model_copy(update={"chunk_id": hit.chunk_id, "excerpt": hit.text})
    store = Store(
        context,
        [citation, hit_citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )
    llm = GapLLM(
        _claim(
            value="Must be an authorized reseller for Dell",
            confidence=0.9,
            chunk_ids=(hit.chunk_id,),
            status="extracted",
        )
    )

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    assert len(_gap_prompts(llm)) == 1
    assert hit.text in _gap_prompts(llm)[0]
    field = output.fields.mfg_for_registration
    assert field.value == "Must be an authorized reseller for Dell"
    assert field.validation_status == FieldValidationStatus.SUPPORTED
    assert [item.chunk_id for item in field.citations] == [hit.chunk_id]
    assert output.fields.bid_bond_requirement.validation_status == FieldValidationStatus.NOT_STATED


def test_gap_check_judges_generic_mentions_as_not_stated_and_cites_them() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(
        update={"chunk_id": uuid4(), "text": "Resellers must meet manufacturer qualifications noted elsewhere."}
    )
    hit_citation = citation.model_copy(update={"chunk_id": hit.chunk_id, "excerpt": hit.text})
    store = Store(
        context,
        [citation, hit_citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )
    llm = GapLLM(
        _claim(
            chunk_ids=(hit.chunk_id,),
            notes="Only a generic reference; no requirement is given.",
            status="not_stated",
        )
    )

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    field = output.fields.mfg_for_registration
    assert field.value == NOT_STATED_ANSWER
    assert field.validation_status == FieldValidationStatus.NOT_STATED
    assert [item.chunk_id for item in field.citations] == [hit.chunk_id]


def test_gap_check_reads_not_found_with_examined_chunks_as_not_stated() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(update={"chunk_id": uuid4(), "text": "Comply with any bond noted elsewhere."})
    hit_citation = citation.model_copy(update={"chunk_id": hit.chunk_id, "excerpt": hit.text})
    store = Store(
        context,
        [citation, hit_citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )
    llm = GapLLM(_claim(chunk_ids=(hit.chunk_id,), notes="Generic only.", status="not_found"))

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    assert len(_gap_prompts(llm)) == 1
    field = output.fields.mfg_for_registration
    assert field.validation_status == FieldValidationStatus.NOT_STATED
    assert [item.chunk_id for item in field.citations] == [hit.chunk_id]


def test_field_rejected_by_the_contract_is_gap_checked() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(update={"chunk_id": uuid4(), "text": "Must be an authorized reseller."})
    hit_citation = citation.model_copy(update={"chunk_id": hit.chunk_id, "excerpt": hit.text})
    store = Store(
        context,
        [citation, hit_citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )

    class ContractBreakingLLM(GapLLM):
        async def generate_response(self, prompt, **kwargs):
            if "earlier extraction did not find" in prompt:
                return await super().generate_response(prompt, **kwargs)
            self.calls.append((prompt, kwargs))
            return _payload(**{"MFG for Registration": {"value": "x", "status": "extracted"}})

    llm = ContractBreakingLLM(
        _claim(
            value="Authorized reseller",
            confidence=0.9,
            chunk_ids=(hit.chunk_id,),
            status="extracted",
        )
    )

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    field = output.fields.mfg_for_registration
    assert field.value == "Authorized reseller"
    assert field.validation_status == FieldValidationStatus.SUPPORTED


def test_gap_check_rejects_citations_outside_the_matched_chunks() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(update={"chunk_id": uuid4(), "text": "Authorized reseller required."})
    hit_citation = citation.model_copy(update={"chunk_id": hit.chunk_id, "excerpt": hit.text})
    store = Store(
        context,
        [citation, hit_citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )
    llm = GapLLM(
        _claim(
            value="Authorized reseller",
            confidence=0.9,
            chunk_ids=(result.chunk_id,),
            status="extracted",
        )
    )

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    field = output.fields.mfg_for_registration
    assert field.value is None
    assert field.validation_status == FieldValidationStatus.UNSUPPORTED
    assert output.status == ExtractionRunStatus.PARTIAL


def test_gap_check_keeps_field_missing_when_the_model_answer_is_invalid() -> None:
    context, result, citation = _fixture()
    hit = result.model_copy(update={"chunk_id": uuid4(), "text": "Authorized reseller required."})
    store = Store(
        context,
        [citation],
        searched_chunks=42,
        text_hits={"MFG for Registration": [hit]},
    )
    llm = GapLLM({"value": "x", "status": "extracted"})

    output = asyncio.run(_service(store, Retriever([result]), llm).extract(context.id))

    assert output.fields.mfg_for_registration.validation_status == FieldValidationStatus.MISSING
