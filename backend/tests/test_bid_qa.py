"""Grounded bid Q&A service and endpoint, using fake retrieval and LLM providers."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.qa import get_bid_qa_service
from app.main import app
from app.retrieval.base import RetrievalUnavailable
from app.schemas.citation import Citation
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import (
    BidIntelligenceProfile,
    IntelligenceProfileField,
    IntelligenceProfileMetadata,
    IntelligenceProfileStatus,
    ProfileFieldStatus,
)
from app.schemas.qa import AskRequest, ChatTurn
from app.schemas.retrieval import RetrievalResponse, RetrievedChunk
from app.services.bid_qa_context import POLICY_NOTES, BidContext, BidRecord
from app.services.bid_qa_service import (
    NO_PASSAGES_ANSWER,
    NO_PROFILE_ANSWER,
    PROFILE_WARNING,
    READ_ONLY_WARNING,
    REPEAT_REMINDER,
    RESPONSE_REMINDER,
    SYSTEM_PROMPT,
    UNSUPPORTED_ANSWER,
    AnswerGenerationFailed,
    BidQAService,
    InvalidCompareBids,
    QASettings,
    search_query,
    strip_citation_markers,
)
from app.services.provider_errors import ProviderRequestError

BID = uuid4()
OTHER_BID = uuid4()


def _chunk(text: str, *, bid_id: UUID = BID, page: int | None = 3) -> RetrievedChunk:
    return RetrievedChunk(
        bid_id=bid_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        file_name="rfp.pdf",
        text=text,
        page_number=page,
        section_title="Submission",
    )


class FakeStore:
    def __init__(self, count: int | None = 2, counts: dict | None = None) -> None:
        self.count = count
        self.counts = counts or {}

    async def indexed_document_count(self, bid_id):
        return self.counts.get(bid_id, self.count)


class FakeRetrieval:
    def __init__(self, chunks=None, error: Exception | None = None, by_bid: dict | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.by_bid = by_bid
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        results = self.by_bid.get(request.bid_id, []) if self.by_bid is not None else self.chunks
        return RetrievalResponse(query=request.query, bid_id=request.bid_id, results=results)


class FakeLLM:
    def __init__(self, reply=None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    async def generate_response(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.kwargs = kwargs
        if self.error:
            raise self.error
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        return reply if isinstance(reply, str) else json.dumps(reply)


class FakeRecords:
    def __init__(self, contexts: dict | None = None) -> None:
        self.contexts = contexts or {}

    async def load(self, bid_id):
        return self.contexts.get(bid_id) or BidContext(bid_id=bid_id, label=f"label {bid_id}")


def _service(store=None, retrieval=None, llm=None, history_turns=6, records=None) -> BidQAService:
    return BidQAService(
        records=records,
        store=store or FakeStore(),
        retrieval=retrieval or FakeRetrieval([_chunk("Proposals are due October 30, 2026.")]),
        llm=llm or FakeLLM({"answer": "Due October 30, 2026 [S1].", "found": True, "cited_sources": ["S1"]}),
        settings=QASettings(
            top_k=8,
            max_history_turns=history_turns,
            max_output_tokens=1200,
            max_passage_characters=50,
            model="test-model",
        ),
    )


def _ask(service, question="When are proposals due?", history=None, compare=None, document_ids=None):
    request = AskRequest(
        question=question,
        history=history or [],
        compare_bid_ids=compare or [],
        document_ids=document_ids or [],
    )
    return asyncio.run(service.ask(BID, request))


def test_answer_keeps_only_cited_retrieved_sources():
    chunks = [_chunk("Due October 30."), _chunk("Bond of 5 percent.", page=None)]
    llm = FakeLLM(
        {"answer": "Due October 30 [S1] with a bond [S2] per [S9].", "found": True, "cited_sources": ["S2", "S9"]}
    )
    response = _ask(_service(retrieval=FakeRetrieval(chunks), llm=llm))

    assert response.found is True
    assert [source.source_id for source in response.sources] == ["S2", "S1"]
    assert "[S9]" not in response.answer
    assert response.answer.startswith("Due October 30 [S1] with a bond [S2]")
    assert response.sources[1].page_number == 3
    assert response.model == "test-model"
    assert llm.kwargs["temperature"] == 0.0


def test_found_answer_without_valid_citation_is_not_returned():
    llm = FakeLLM({"answer": "The bid is due Friday [S7].", "found": True, "cited_sources": []})
    response = _ask(_service(llm=llm))

    assert response.found is False
    assert response.answer == UNSUPPORTED_ANSWER
    assert response.sources == []


def test_not_found_answer_is_preserved():
    llm = FakeLLM({"answer": "The passages do not state a bond amount.", "found": False, "cited_sources": []})
    response = _ask(_service(llm=llm), question="What bond is required?")

    assert response.found is False
    assert response.answer == "The passages do not state a bond amount."


def test_placeholder_not_found_answer_is_replaced():
    llm = FakeLLM({"answer": "found", "found": False, "cited_sources": []})
    response = _ask(_service(llm=llm))

    assert response.found is False
    assert response.answer == UNSUPPORTED_ANSWER


def test_no_passages_skips_the_model_call():
    llm = FakeLLM({"answer": "x", "found": True})
    response = _ask(_service(retrieval=FakeRetrieval([]), llm=llm))

    assert response.found is False
    assert response.answer == NO_PASSAGES_ANSWER
    assert llm.prompts == []


def test_results_from_other_bids_are_dropped():
    retrieval = FakeRetrieval([_chunk("Another bid's due date", bid_id=OTHER_BID)])
    llm = FakeLLM({"answer": "x", "found": True, "cited_sources": ["S1"]})
    response = _ask(_service(retrieval=retrieval, llm=llm))

    assert response.answer == NO_PASSAGES_ANSWER
    assert llm.prompts == []
    assert retrieval.requests[0].bid_id == BID


def test_history_is_capped_and_previous_question_guides_search():
    history = [
        ChatTurn(role="user", content="old question"),
        ChatTurn(role="assistant", content="old answer"),
        ChatTurn(role="user", content="What insurance is required?"),
        ChatTurn(role="assistant", content="General liability [S1]."),
    ]
    retrieval = FakeRetrieval([_chunk("Bond of 5 percent of the bid amount is required here.")])
    llm = FakeLLM({"answer": "5 percent [S1].", "found": True, "cited_sources": ["S1"]})
    _ask(_service(retrieval=retrieval, llm=llm, history_turns=2), question="And the bond?", history=history)

    assert retrieval.requests[0].query.startswith("And the bond?\nWhat insurance is required?")
    assert "bid bond" in retrieval.requests[0].query
    prompt = llm.prompts[0]
    assert "old question" not in prompt
    assert "USER: What insurance is required?" in prompt
    assert "ASSISTANT: General liability." in prompt
    assert "[S1]." not in prompt.split("Source passages:")[0]
    assert "Bond of 5 percent of the bid amount is required h" in prompt
    assert "Bond of 5 percent of the bid amount is required here." not in prompt


def test_search_query_without_history_is_the_question():
    assert search_query("Due date?", []) == "Due date?"


@pytest.mark.parametrize(
    ("question", "combined"),
    [
        ("What is the contract duration, including renewal options?", False),
        ("What payment terms are specified in this bid's documents?", False),
        ("List all mandatory documents and affidavits required for submission.", False),
        ("And the bond?", True),
        ("Is that required?", True),
        ("Is it mandatory for every vendor submitting a proposal?", True),
        ("Also what are the insurance requirements for vendors?", True),
    ],
)
def test_only_follow_up_questions_include_the_previous_question(question, combined):
    history = [ChatTurn(role="user", content="What products are requested?")]
    query = search_query(question, history)
    assert (query != question) is combined
    if combined:
        assert query == f"{question}\nWhat products are requested?"


def test_duplicate_passages_are_numbered_once():
    chunks = [
        _chunk("Sealed  envelope by the date\nstated."),
        _chunk("sealed envelope by the date stated."),
        _chunk("Bond of 5 percent."),
    ]
    llm = FakeLLM({"answer": "Sealed envelope [S1], bond [S2].", "found": True, "cited_sources": ["S1", "S2"]})
    response = _ask(_service(retrieval=FakeRetrieval(chunks), llm=llm))

    assert "[S3]" not in llm.prompts[0]
    assert [source.chunk_id for source in response.sources] == [chunks[0].chunk_id, chunks[2].chunk_id]


@pytest.mark.parametrize(
    "reply",
    [
        {"1. Bid Number": {"value": "JA-207652", "cited_sources": ["S1"]}},
        {"answer": "Due October 30 [S1]."},
        "not json",
    ],
)
def test_reply_without_the_answer_envelope_fails_instead_of_reporting_not_found(reply):
    with pytest.raises(AnswerGenerationFailed, match="unexpected format"):
        _ask(_service(llm=FakeLLM(reply)))


def test_prompt_keeps_user_requested_formats_inside_the_answer():
    llm = FakeLLM({"answer": "Due [S1].", "found": True, "cited_sources": ["S1"]})
    _ask(_service(llm=llm), question="Return the due date as JSON.")

    assert 'put that formatted content inside the "answer" string' in SYSTEM_PROMPT
    assert llm.prompts[0].endswith(RESPONSE_REMINDER)
    assert llm.prompts[0].index("Question: Return the due date as JSON.") < llm.prompts[0].index(RESPONSE_REMINDER)


def test_unquoted_cited_ids_are_repaired():
    llm = FakeLLM('{"answer": "Due [S1].", "found": true, "cited_sources": [S1, S9]}')
    response = _ask(_service(llm=llm))

    assert response.found is True
    assert [source.source_id for source in response.sources] == ["S1"]


def test_unquoted_ids_outside_cited_sources_still_fail():
    with pytest.raises(AnswerGenerationFailed):
        _ask(_service(llm=FakeLLM('{"answer": [S1], "found": true, "cited_sources": []}')))


def test_fenced_json_is_accepted():
    llm = FakeLLM('```json\n{"answer": "Due [S1]", "found": true, "cited_sources": ["S1"]}\n```')
    assert _ask(_service(llm=llm)).found is True


def _citation(excerpt: str, *, bid_id: UUID = BID, page: int = 2, file_name: str = "rfp.pdf") -> Citation:
    return Citation(
        bid_id=bid_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        file_name=file_name,
        page_number=page,
        excerpt=excerpt,
    )


def _profile(bid_id: UUID = BID) -> BidIntelligenceProfile:
    fields = {
        name: IntelligenceProfileField(
            validation_status=ProfileFieldStatus.NOT_FOUND, review_recommendation="Not stated."
        )
        for name in ASSIGNMENT_FIELD_NAMES
    }
    fields["Bid Number"] = IntelligenceProfileField(
        value="JA-207652",
        validation_status=ProfileFieldStatus.VALIDATED,
        confidence_score=0.95,
        supporting_citations=[_citation("Solicitation JA-207652", bid_id=bid_id, page=1)],
        review_recommendation="None.",
    )
    fields["Due Date"] = IntelligenceProfileField(
        value="July 9, 2024 at 2:00 PM CST",
        validation_status=ProfileFieldStatus.NEEDS_REVIEW,
        confidence_score=0.8,
        supporting_citations=[_citation("July 9, 2024 at 2:00 PM CST", bid_id=bid_id, file_name="addendum2.pdf", page=1)],
        contradicting_citations=[_citation("Solicitation Due 27-JUN-2024 14:00:00", bid_id=bid_id)],
        review_recommendation="Verify the due date.",
    )
    metadata = IntelligenceProfileMetadata(
        bid_id=bid_id,
        bid_identifier="bid6",
        profile_id=uuid4(),
        generated_at=datetime.now(UTC),
        extraction_run_id=uuid4(),
        source_document_count=2,
        status=IntelligenceProfileStatus.NEEDS_REVIEW,
        review_required=True,
    )
    return BidIntelligenceProfile(metadata=metadata, fields=fields)


def test_strip_citation_markers_leaves_clean_text():
    assert strip_citation_markers("Due Oct 30 [S1], bond 5% [R2] [P1].") == "Due Oct 30, bond 5%."


def test_repeated_answer_is_regenerated_once():
    history = [
        ChatTurn(role="user", content="When are proposals due?"),
        ChatTurn(role="assistant", content="Due October 30, 2026 [S1]."),
    ]
    llm = FakeLLM(
        [
            {"answer": "Due October 30, 2026 [S2].", "found": True, "cited_sources": ["S2"]},
            {"answer": "Submit a sealed envelope [S1].", "found": True, "cited_sources": ["S1"]},
        ]
    )
    response = _ask(_service(llm=llm), question="How must responses be submitted?", history=history)

    assert response.answer == "Submit a sealed envelope [S1]."
    assert len(llm.prompts) == 2
    assert llm.prompts[1].endswith(REPEAT_REMINDER)


def test_answer_repeated_twice_is_not_returned():
    history = [
        ChatTurn(role="user", content="When are proposals due?"),
        ChatTurn(role="assistant", content="Due October 30, 2026 [S1]."),
    ]
    repeat = {"answer": "Due October 30, 2026 [S1].", "found": True, "cited_sources": ["S1"]}
    llm = FakeLLM([repeat, repeat])
    response = _ask(_service(llm=llm), question="How must responses be submitted?", history=history)

    assert response.found is False
    assert response.answer == UNSUPPORTED_ANSWER


def test_near_identical_answer_counts_as_a_repeat():
    history = [
        ChatTurn(role="user", content="What changes if we consider the addendum?"),
        ChatTurn(role="assistant", content="The provided documents do not mention any addendum for this bid [S1]."),
    ]
    llm = FakeLLM(
        [
            {"answer": "The provided documents do not specify any addendum for this bid.", "found": False},
            {"answer": "Rechecked: the passages list no addendum documents for this bid.", "found": False},
        ]
    )
    response = _ask(_service(llm=llm), question="Are you certain? Recheck the original document.", history=history)

    assert len(llm.prompts) == 2
    assert response.answer.startswith("Rechecked")


@pytest.mark.parametrize(
    ("question", "warned"),
    [
        ("Create a review item for the ambiguous deadline.", True),
        ("Please approve the proposed value.", True),
        ("Reject this finding because its evidence is insufficient.", True),
        ("Request re-evaluation of this field.", True),
        ("Re-run the same extraction several times.", True),
        ("What happens if a reviewer approves this field?", False),
        ("When are proposals due?", False),
    ],
)
def test_action_requests_get_a_read_only_warning(question, warned):
    response = _ask(_service(), question=question)
    assert (READ_ONLY_WARNING in response.warnings) is warned


def test_asking_the_same_question_again_may_repeat_the_answer():
    history = [
        ChatTurn(role="user", content="When are proposals due?"),
        ChatTurn(role="assistant", content="Due October 30, 2026 [S1]."),
    ]
    llm = FakeLLM({"answer": "Due October 30, 2026 [S1].", "found": True, "cited_sources": ["S1"]})
    response = _ask(_service(llm=llm), history=history)

    assert response.found is True
    assert len(llm.prompts) == 1


def test_cited_records_resolve_to_their_stored_citations():
    cited = BidRecord(
        text='Profile field "Due Date": value=July 9; status=needs_review',
        citations=(_citation("Solicitation Due 27-JUN-2024"), _citation("July 9, 2024", page=1)),
    )
    uncited = BidRecord(text="Open review item (missing_evidence); reason=No citation stored")
    records = FakeRecords({BID: BidContext(bid_id=BID, label="JA-207652 - Devices", records=(cited, uncited))})
    llm = FakeLLM(
        {"answer": "The due date needs review [R1]; see also [R2].", "found": True, "cited_sources": ["R1", "R2"]}
    )
    response = _ask(_service(llm=llm, records=records), question="Is the due date confirmed?")

    assert "[R1] (Bid: JA-207652 - Devices) Profile field" in llm.prompts[0]
    assert "Sources: rfp.pdf page 2; rfp.pdf page 1." in llm.prompts[0]
    assert [(source.source_id, source.kind, source.page_number) for source in response.sources] == [
        ("R1", "record", 2),
        ("R1", "record", 1),
        ("R2", "record", None),
    ]
    assert response.sources[0].excerpt == "Solicitation Due 27-JUN-2024"
    assert response.sources[0].record == cited.text
    assert response.sources[2].document_id is None
    assert response.sources[2].record == uncited.text


def test_records_are_evidence_even_without_retrieved_passages():
    records = FakeRecords(
        {BID: BidContext(bid_id=BID, label="Bid", records=(BidRecord(text="Open review item"),))}
    )
    llm = FakeLLM({"answer": "One review item is open [R1].", "found": True, "cited_sources": ["R1"]})
    response = _ask(_service(retrieval=FakeRetrieval([]), llm=llm, records=records), question="What is open?")

    assert response.found is True
    assert len(llm.prompts) == 1


def test_policy_notes_are_included_only_for_system_questions():
    llm = FakeLLM({"answer": "No [P1].", "found": True, "cited_sources": ["P1"]})
    response = _ask(_service(llm=llm), question="Can a review item with an invalid citation be approved?")

    assert POLICY_NOTES[0] in llm.prompts[0]
    assert response.sources[0].kind == "policy"
    assert response.sources[0].excerpt == POLICY_NOTES[0]

    llm = FakeLLM({"answer": "Due [S1].", "found": True, "cited_sources": ["S1"]})
    _ask(_service(llm=llm))
    assert "System policy notes" not in llm.prompts[0]


def test_prompt_rules_cover_conflicts_actions_and_scope():
    assert "list every value with its citation" in SYSTEM_PROMPT
    assert "Do not convert time zones" in SYSTEM_PROMPT
    assert "Never apply one bid's requirement or value to another bid" in SYSTEM_PROMPT
    assert "You cannot take actions" in SYSTEM_PROMPT
    llm = FakeLLM({"answer": "Due [S1].", "found": True, "cited_sources": ["S1"]})
    _ask(_service(llm=llm))
    assert "Only this bid is in scope." in llm.prompts[0]


def test_search_query_is_expanded_with_procurement_terms():
    retrieval = FakeRetrieval([_chunk("Solicitation Due 27-JUN-2024")])
    _ask(_service(retrieval=retrieval), question="What was the original submission deadline?")

    query = retrieval.requests[0].query
    assert query.startswith("What was the original submission deadline?\n")
    assert "solicitation due" in query


def test_compare_searches_each_bid_and_labels_passages():
    other_doc = uuid4()
    retrieval = FakeRetrieval(
        by_bid={
            BID: [_chunk("Due October 30.")],
            OTHER_BID: [_chunk("Due November 2.", bid_id=OTHER_BID), _chunk("stray", bid_id=BID)],
        }
    )
    records = FakeRecords(
        {
            BID: BidContext(bid_id=BID, label="JA-207652 - Devices"),
            OTHER_BID: BidContext(bid_id=OTHER_BID, label="E20 - Laptops"),
        }
    )
    llm = FakeLLM(
        {"answer": "Oct 30 [S1] versus Nov 2 [S2].", "found": True, "cited_sources": ["S1", "S2"]}
    )
    response = _ask(
        _service(retrieval=retrieval, llm=llm, records=records),
        question="Compare the due dates",
        compare=[OTHER_BID],
        document_ids=[other_doc],
    )

    assert [request.bid_id for request in retrieval.requests] == [BID, OTHER_BID]
    assert retrieval.requests[0].document_ids == [other_doc]
    assert retrieval.requests[1].document_ids == []
    prompt = llm.prompts[0]
    assert "- E20 - Laptops (added for comparison)" in prompt
    assert "[S2] (Bid: E20 - Laptops, rfp.pdf" in prompt
    assert "stray" not in prompt
    assert "Only this bid is in scope." not in prompt
    assert [(source.bid_id, source.bid_label) for source in response.sources] == [
        (BID, "JA-207652 - Devices"),
        (OTHER_BID, "E20 - Laptops"),
    ]


@pytest.mark.parametrize(
    ("compare", "message"),
    [([BID], "itself"), ([OTHER_BID, uuid4()], "at most 1")],
)
def test_invalid_compare_bids_are_rejected(compare, message):
    with pytest.raises(InvalidCompareBids, match=message):
        _ask(_service(), compare=compare)


def test_compare_bid_ids_are_deduplicated():
    request = AskRequest(question="Compare", compare_bid_ids=[OTHER_BID, OTHER_BID])
    assert request.compare_bid_ids == [OTHER_BID]


def test_profile_request_is_answered_from_the_stored_profile():
    llm = FakeLLM({"answer": "x", "found": True})
    retrieval = FakeRetrieval([_chunk("Due")])
    records = FakeRecords({BID: BidContext(bid_id=BID, label="JA-207652", profile=_profile())})
    response = _ask(
        _service(retrieval=retrieval, llm=llm, records=records),
        question="Extract all 20 required fields from this bid as JSON.",
    )

    assert llm.prompts == []
    assert retrieval.requests == []
    fields = json.loads(response.answer)
    assert tuple(fields) == ASSIGNMENT_FIELD_NAMES
    due = fields["Due Date"]
    assert due["validation_status"] == "needs_review"
    assert [(item["source_id"], item["role"]) for item in due["citations"]] == [
        ("R3", "supporting"),
        ("R3", "contradicting"),
    ]
    assert fields["Title"] == {
        "value": None,
        "validation_status": "not_found",
        "confidence_score": None,
        "citations": [],
    }
    assert response.found is True
    assert {source.source_id for source in response.sources} == {"R1", "R3"}
    assert PROFILE_WARNING in response.warnings
    assert "Profile status: needs_review" in response.warnings


def test_profile_request_without_a_profile_does_not_extract():
    llm = FakeLLM({"answer": "x", "found": True})
    response = _ask(_service(llm=llm), question="List the 20 fields for this bid.")

    assert response.found is False
    assert response.answer == NO_PROFILE_ANSWER
    assert llm.prompts == []


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def _override(service):
    app.dependency_overrides[get_bid_qa_service] = lambda: service


def test_endpoint_returns_grounded_answer(client):
    _override(_service())
    response = client.post(f"/api/v1/bids/{BID}/ask", json={"question": "When are proposals due?"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["sources"][0]["source_id"] == "S1"
    assert body["sources"][0]["file_name"] == "rfp.pdf"


@pytest.mark.parametrize(
    ("service", "status", "code"),
    [
        (lambda: _service(store=FakeStore(None)), 404, None),
        (lambda: _service(store=FakeStore(0)), 409, "bid_not_indexed"),
        (lambda: _service(retrieval=FakeRetrieval(error=RetrievalUnavailable(["down"]))), 503, "retrieval_unavailable"),
        (lambda: _service(llm=FakeLLM("not json")), 502, "answer_generation_failed"),
        (lambda: _service(llm=FakeLLM(error=ProviderRequestError("LLM request timed out"))), 502, "answer_generation_failed"),
    ],
)
def test_endpoint_error_mapping(client, service, status, code):
    _override(service())
    response = client.post(f"/api/v1/bids/{BID}/ask", json={"question": "Due date?"})

    assert response.status_code == status
    if code:
        assert response.json()["detail"]["code"] == code


@pytest.mark.parametrize(
    ("compare", "store", "status", "code", "text"),
    [
        ([str(BID)], FakeStore(), 422, "invalid_compare_bids", "itself"),
        ([str(OTHER_BID), str(uuid4())], FakeStore(), 422, "invalid_compare_bids", "at most"),
        ([str(OTHER_BID)], FakeStore(counts={OTHER_BID: 0}), 409, "bid_not_indexed", "comparison"),
        ([str(OTHER_BID)], FakeStore(counts={OTHER_BID: None}), 404, None, "Comparison bid not found"),
    ],
)
def test_endpoint_compare_errors(client, compare, store, status, code, text):
    _override(_service(store=store))
    response = client.post(
        f"/api/v1/bids/{BID}/ask", json={"question": "Compare due dates", "compare_bid_ids": compare}
    )

    assert response.status_code == status
    detail = response.json()["detail"]
    if code:
        assert detail["code"] == code
    assert text in json.dumps(detail)


def test_endpoint_returns_record_sources(client):
    records = FakeRecords({BID: BidContext(bid_id=BID, label="Bid", records=(BidRecord(text="Open review item"),))})
    llm = FakeLLM({"answer": "One item is open [R1].", "found": True, "cited_sources": ["R1"]})
    _override(_service(llm=llm, records=records))
    response = client.post(f"/api/v1/bids/{BID}/ask", json={"question": "What is open?"})

    source = response.json()["sources"][0]
    assert source["kind"] == "record"
    assert source["bid_label"] == "Bid"
    assert source["document_id"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": ""},
        {"question": "x" * 1001},
        {"question": "ok", "history": [{"role": "system", "content": "hi"}]},
        {"question": "ok", "history": [{"role": "user", "content": "hi"}] * 41},
    ],
)
def test_request_validation(client, payload):
    _override(_service())
    assert client.post(f"/api/v1/bids/{BID}/ask", json=payload).status_code == 422
