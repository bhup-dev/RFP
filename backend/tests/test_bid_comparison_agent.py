import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.bid_comparison_agent import (
    ComparisonDependencies,
    build_bid_comparison_graph,
    report_from_state,
)
from app.agents.comparison_state import COMPARISON_STAGES, initial_comparison_state
from app.schemas.citation import Citation
from app.schemas.comparison import (
    BidComparisonRequest,
    ComparisonDimension,
    DifferenceClassification,
)
from app.schemas.extraction import ASSIGNMENT_FIELD_NAMES
from app.schemas.intelligence import (
    BidIntelligenceProfile,
    IntelligenceProfileField,
    IntelligenceProfileMetadata,
    ProfileAddendumChange,
)
from app.services.specialized_agent_store import CitationCheck
from app.api.comparison import get_comparison_dependencies
from app.main import app


def _citation(bid_id):
    return Citation(
        bid_id=bid_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        file_name="arbitrary.pdf",
        page_number=1,
        excerpt="Persisted evidence excerpt",
    )


def _profile(
    bid_id,
    *,
    values=None,
    status="ready",
    validation=True,
    conflict_field=None,
):
    values = values or {}
    fields = {}
    for name in ASSIGNMENT_FIELD_NAMES:
        value = values.get(name)
        citation = _citation(bid_id)
        changes = []
        field_status = "validated" if value is not None else "not_found"
        contradicting = []
        if name == conflict_field:
            field_status = "conflicting"
            contradicting = [citation]
            changes = [
                ProfileAddendumChange(
                    finding_id=uuid4(),
                    analysis_run_id=uuid4(),
                    classification="conflict",
                    topic=name,
                    original_value=value,
                    amended_value="conflicting amendment",
                    addendum_sources=[citation],
                )
            ]
        fields[name] = IntelligenceProfileField(
            value=value,
            validation_status=field_status,
            supporting_citations=[citation] if value is not None else [],
            contradicting_citations=contradicting,
            review_recommendation="Review." if field_status == "conflicting" else "None.",
            addendum_changes=changes,
        )
    return BidIntelligenceProfile(
        metadata=IntelligenceProfileMetadata(
            bid_id=bid_id,
            bid_identifier=f"package-{bid_id}",
            profile_id=uuid4(),
            generated_at=datetime.now(UTC),
            extraction_run_id=uuid4() if status != "incomplete" else None,
            validation_run_id=uuid4() if validation else None,
            addendum_analysis_run_id=uuid4() if conflict_field else None,
            source_document_count=1,
            source_document_ids=[uuid4()],
            status=status,
            review_required=conflict_field is not None or status != "ready",
        ),
        fields=fields,
    )


class Profiles:
    def __init__(self, profiles):
        self.profiles = profiles
        self.calls = []

    async def build(self, bid_id, extraction_run_id=None):
        self.calls.append((bid_id, extraction_run_id))
        return self.profiles[bid_id]


class Persistence:
    def __init__(self, bid_ids):
        self.bid_ids = set(bid_ids)
        self.references = []
        self.report = None
        self.created = None

    async def create_run(self, state):
        self.created = state

    async def existing_bid_ids(self, _bid_ids):
        return self.bid_ids

    async def save_profile_references(self, _comparison_id, references):
        self.references = references

    async def save_report(self, report):
        self.report = report


class Evidence:
    def __init__(self, reason=None, error=None):
        self.reason = reason
        self.error = error

    async def validate_citations(self, _bid_id, citations):
        if self.error:
            raise self.error
        return [
            CitationCheck(item, self.reason is None, self.reason)
            for item in citations
        ]


class LLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"comparisons": [], "warnings": []}
        self.error = error
        self.calls = 0
        self.last_kwargs = {}

    async def generate_response(self, *_args, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        return (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )


class SlowLLM:
    calls = 0

    async def generate_response(self, *_args, **_kwargs):
        self.calls += 1
        await asyncio.sleep(0.05)
        return "{}"


class ReviewManager:
    def __init__(self):
        self.item_ids = [uuid4(), uuid4()]
        self.resolved = False

    async def create_items(self, _state):
        return self.item_ids

    async def all_resolved(self, _comparison_id):
        return self.resolved


def _dependencies(profiles, llm=None, evidence=None, existing=None, max_bids=5):
    ids = list(profiles)
    persistence = Persistence(ids if existing is None else existing)
    return ComparisonDependencies(
        Profiles(profiles),
        persistence,
        evidence or Evidence(),
        llm or LLM(),
        max_bids=max_bids,
        semantic_timeout_seconds=1,
        max_semantic_rows=20,
        max_output_tokens=1000,
    )


def _run(request, dependencies, checkpointer=None):
    state = initial_comparison_state(uuid4(), request, datetime.now(UTC))
    graph = build_bid_comparison_graph(dependencies, checkpointer)
    config = {"configurable": {"thread_id": str(state["comparison_id"])}}
    result = asyncio.run(graph.ainvoke(state, config=config))
    return result, report_from_state(result), graph, config


def test_compare_two_bids_classifies_exact_missing_products_and_deadlines():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(
                first,
                values={
                    "Bid Number": "A-1",
                    "Product": "Router X",
                    "Model_no": "RX-100",
                    "Due Date": "2026-10-31T14:00:00-04:00",
                    "Payment Terms": "Net 30",
                },
            ),
            second: _profile(
                second,
                values={
                    "Bid Number": "B-2",
                    "Product": "Switch Y",
                    "Model_no": "SY-200",
                    "Due Date": "2026-11-01T14:00:00-04:00",
                },
            ),
        }
    )
    request = BidComparisonRequest(
        bid_ids=[first, second],
        dimensions=["scope", "products", "commercial_terms"],
    )
    _, report, graph, _ = _run(request, deps)
    rows = {row.attribute: row for row in report.comparison_rows}
    assert set(COMPARISON_STAGES) <= set(graph.get_graph().nodes)
    assert rows["Bid Number"].classification == DifferenceClassification.DIFFERENT
    assert rows["Model_no"].classification == DifferenceClassification.DIFFERENT
    assert rows["Product"].classification == DifferenceClassification.NOT_COMPARABLE
    assert rows["Payment Terms"].classification == DifferenceClassification.MISSING
    assert rows["Due Date"].classification == DifferenceClassification.DIFFERENT
    assert deps.profiles.calls == [(first, None), (second, None)]


def test_three_bid_comparison_preserves_stable_per_bid_values():
    ids = [uuid4(), uuid4(), uuid4()]
    deps = _dependencies(
        {
            bid_id: _profile(
                bid_id, values={"Part_no": f"PART-{index}", "Product": "Router"}
            )
            for index, bid_id in enumerate(ids)
        }
    )
    _, report, _, _ = _run(
        BidComparisonRequest(bid_ids=ids, dimensions=["products"]), deps
    )
    part = next(row for row in report.comparison_rows if row.attribute == "Part_no")
    assert [item.bid_id for item in part.values] == ids
    assert part.classification == DifferenceClassification.DIFFERENT


def test_quantities_units_and_contract_terms_compare_deterministically():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(
                first,
                values={
                    "Product": {
                        "name": "Router",
                        "quantity": 10,
                        "unit": "each",
                    },
                    "Term of Bid": "24 months",
                },
            ),
            second: _profile(
                second,
                values={
                    "Product": {
                        "name": "Router",
                        "quantity": 12,
                        "unit": "each",
                    },
                    "Term of Bid": "24 months",
                },
            ),
        }
    )
    _, report, _, _ = _run(
        BidComparisonRequest(
            bid_ids=[first, second],
            dimensions=["products", "commercial_terms"],
        ),
        deps,
    )
    rows = {row.attribute: row for row in report.comparison_rows}
    assert rows["Product"].classification == DifferenceClassification.DIFFERENT
    assert rows["Term of Bid"].classification == DifferenceClassification.EQUIVALENT


def test_semantic_comparison_updates_only_supplied_narrative_row():
    first, second = uuid4(), uuid4()
    llm = LLM(
        {
            "comparisons": [
                {
                    "attribute": "Bid Summary",
                    "classification": "equivalent",
                    "explanation": "Both describe managed network installation.",
                    "confidence": 0.8,
                }
            ],
            "warnings": [],
        }
    )
    deps = _dependencies(
        {
            first: _profile(first, values={"Bid Summary": "Install managed network"}),
            second: _profile(second, values={"Bid Summary": "Managed network deployment"}),
        },
        llm=llm,
    )
    _, report, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second], dimensions=["scope"]), deps
    )
    summary = next(
        row for row in report.comparison_rows if row.attribute == "Bid Summary"
    )
    assert summary.classification == DifferenceClassification.EQUIVALENT
    assert llm.calls == 1
    assert "untrusted document content" in llm.last_kwargs["system"]


def test_malformed_semantic_output_preserves_deterministic_uncertainty():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(first, values={"Title": "Network system"}),
            second: _profile(second, values={"Title": "Networking platform"}),
        },
        llm=LLM("not-json"),
    )
    _, report, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second], dimensions=["scope"]), deps
    )
    title = next(row for row in report.comparison_rows if row.attribute == "Title")
    assert title.classification == DifferenceClassification.UNCERTAIN
    assert any("Semantic comparison unavailable" in item for item in report.warnings)


def test_semantic_timeout_preserves_deterministic_rows():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(first, values={"Bid Summary": "Network deployment"}),
            second: _profile(second, values={"Bid Summary": "Deploy a network"}),
        },
        llm=SlowLLM(),
    )
    deps.semantic_timeout_seconds = 0.001
    _, report, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second], dimensions=["scope"]), deps
    )
    summary = next(
        row for row in report.comparison_rows if row.attribute == "Bid Summary"
    )
    assert summary.classification == DifferenceClassification.UNCERTAIN
    assert any("Semantic comparison unavailable" in item for item in report.warnings)


def test_incomplete_profile_is_rejected_or_explicitly_allowed():
    first, second = uuid4(), uuid4()
    profiles = {
        first: _profile(first, values={"Bid Number": "A"}),
        second: _profile(second, status="incomplete", validation=False),
    }
    failed, report, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second]), _dependencies(profiles)
    )
    assert failed["error_code"] == "profile_unavailable"
    assert report.status.value == "failed"

    _, allowed, _, _ = _run(
        BidComparisonRequest(
            bid_ids=[first, second], allow_incomplete_profiles=True
        ),
        _dependencies(profiles),
    )
    assert allowed.status.value == "incomplete"


def test_conflicting_addendum_takes_precedence_over_value_difference():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(
                first,
                values={"Due Date": "2026-10-31T14:00:00-04:00"},
                conflict_field="Due Date",
            ),
            second: _profile(
                second, values={"Due Date": "2026-11-01T14:00:00-04:00"}
            ),
        }
    )
    _, report, _, _ = _run(
        BidComparisonRequest(
            bid_ids=[first, second], dimensions=["commercial_terms", "addenda"]
        ),
        deps,
    )
    due_rows = [row for row in report.comparison_rows if row.attribute == "Due Date"]
    assert due_rows
    assert all(
        row.classification == DifferenceClassification.CONFLICTING for row in due_rows
    )
    assert "Due Date" in report.unresolved_conflicts


def test_timezone_missing_deadline_is_uncertain_not_different():
    first, second = uuid4(), uuid4()
    deps = _dependencies(
        {
            first: _profile(first, values={"Due Date": "2026-10-31 14:00"}),
            second: _profile(second, values={"Due Date": "2026-10-31T14:00:00Z"}),
        }
    )
    _, report, _, _ = _run(
        BidComparisonRequest(
            bid_ids=[first, second], dimensions=["commercial_terms"]
        ),
        deps,
    )
    due = next(row for row in report.comparison_rows if row.attribute == "Due Date")
    assert due.classification == DifferenceClassification.UNCERTAIN


def test_invalid_citations_are_removed_and_evidence_failure_routes_to_failed():
    first, second = uuid4(), uuid4()
    profiles = {
        first: _profile(first, values={"Model_no": "A"}),
        second: _profile(second, values={"Model_no": "B"}),
    }
    _, invalid, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second], dimensions=["products"]),
        _dependencies(profiles, evidence=Evidence("citation belongs to another bid")),
    )
    model = next(row for row in invalid.comparison_rows if row.attribute == "Model_no")
    assert model.classification == DifferenceClassification.UNCERTAIN
    assert all(not value.supporting_citations for value in model.values)

    failed, report, _, _ = _run(
        BidComparisonRequest(bid_ids=[first, second], dimensions=["products"]),
        _dependencies(profiles, evidence=Evidence(error=RuntimeError("db failed"))),
    )
    assert failed["error_code"] == "evidence_validation_failed"
    assert report.status.value == "failed"


def test_unknown_and_excess_bid_requests_route_to_validation_failure():
    ids = [uuid4(), uuid4()]
    profiles = {item: _profile(item) for item in ids}
    missing, _, _, _ = _run(
        BidComparisonRequest(bid_ids=ids),
        _dependencies(profiles, existing={ids[0]}),
    )
    assert missing["error_code"] == "bid_not_found"

    excess_ids = [uuid4(), uuid4(), uuid4()]
    excess_profiles = {item: _profile(item) for item in excess_ids}
    excess, _, _, _ = _run(
        BidComparisonRequest(bid_ids=excess_ids),
        _dependencies(excess_profiles, max_bids=2),
    )
    assert excess["error_code"] == "too_many_bids"


def test_request_rejects_duplicate_ids_and_unrequested_run_reference():
    bid_id = uuid4()
    with pytest.raises(ValidationError, match="distinct"):
        BidComparisonRequest(bid_ids=[bid_id, bid_id])
    with pytest.raises(ValidationError, match="unrequested bid"):
        BidComparisonRequest(
            bid_ids=[bid_id, uuid4()],
            extraction_run_ids={uuid4(): uuid4()},
        )


def test_comparison_graph_checkpoint_retains_final_report_state():
    ids = [uuid4(), uuid4()]
    saver = InMemorySaver()
    result, report, graph, config = _run(
        BidComparisonRequest(bid_ids=ids, dimensions=["products"]),
        _dependencies(
            {item: _profile(item, values={"Product": "Router"}) for item in ids}
        ),
        saver,
    )
    snapshot = asyncio.run(graph.aget_state(config))
    assert snapshot.values["comparison_id"] == result["comparison_id"]
    assert snapshot.values["status"] == report.status.value


def test_comparison_graph_interrupt_requires_persisted_resolution_before_resume():
    first, second = uuid4(), uuid4()
    manager = ReviewManager()
    deps = _dependencies(
        {
            first: _profile(
                first,
                values={"Due Date": "June 1"},
                conflict_field="Due Date",
            ),
            second: _profile(second, values={"Due Date": "June 2"}),
        }
    )
    deps.review_manager = manager
    deps.human_review_interrupt_enabled = True
    request = BidComparisonRequest(
        bid_ids=[first, second], dimensions=["commercial_terms"]
    )
    state = initial_comparison_state(uuid4(), request, datetime.now(UTC))
    graph = build_bid_comparison_graph(deps, InMemorySaver())
    config = {"configurable": {"thread_id": str(state["comparison_id"])}}

    interrupted = asyncio.run(graph.ainvoke(state, config=config))
    assert interrupted["status"] == "pending_review"
    assert interrupted["review_item_ids"] == manager.item_ids
    assert interrupted["__interrupt__"]

    bypass_attempt = asyncio.run(
        graph.ainvoke(Command(resume={"approved": True}), config=config)
    )
    assert bypass_attempt["status"] == "pending_review"
    assert bypass_attempt["__interrupt__"]

    manager.resolved = True
    completed = asyncio.run(
        graph.ainvoke(Command(resume={"approved": True}), config=config)
    )
    assert completed["status"] == "completed_with_warnings"
    assert completed["review_status"] == "resolved"


def test_comparison_api_returns_report_and_404_for_unknown_bid():
    ids = [uuid4(), uuid4()]
    deps = _dependencies(
        {
            item: _profile(item, values={"Model_no": f"M-{index}"})
            for index, item in enumerate(ids)
        }
    )
    app.dependency_overrides[get_comparison_dependencies] = lambda: deps
    try:
        response = TestClient(app).post(
            "/api/v1/bids/compare",
            json={
                "bid_ids": [str(item) for item in ids],
                "dimensions": ["products"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["bid_ids"] == [str(item) for item in ids]
    assert response.json()["profile_references"]

    unknown_deps = _dependencies(
        {item: _profile(item) for item in ids}, existing=set()
    )
    app.dependency_overrides[get_comparison_dependencies] = lambda: unknown_deps
    try:
        response = TestClient(app).post(
            "/api/v1/bids/compare",
            json={"bid_ids": [str(item) for item in ids]},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert unknown_deps.persistence.report.status.value == "failed"
