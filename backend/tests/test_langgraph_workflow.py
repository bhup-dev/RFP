import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from app.agents.checkpointing import get_checkpointer
from app.agents.state import STAGES, initial_state
from app.agents.workflow import WorkflowDependencies, build_bid_intelligence_graph
from app.config import Settings
from app.services.addendum_analysis_service import NoAddendaAvailable
from app.services.orchestration_store import BidWorkflowContext
from app.schemas.specialized_agents import (
    AgentExecutionStatus,
    EvidenceReviewResult,
    SpecializedAgentName,
    SpecializedAgentResult,
    SpecializedFinding,
)


class Store:
    def __init__(self, context):
        self.context = context
        self.saved = []

    async def load_bid_context(self, _bid_id):
        return self.context

    async def save_state(self, state):
        self.saved.append(state.copy())


class Service:
    def __init__(self, method, result=None, error=None):
        self.method = method
        self.result = result
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        if name != self.method:
            raise AttributeError(name)

        async def call(*args):
            self.calls.append(args)
            if self.error:
                raise self.error
            return self.result

        return call


class Fields:
    def model_dump(self, **_kwargs):
        return {"Bid Number": {"value": "SYN-1"}, "Title": {"value": None}}


class Summary:
    def model_dump(self, **_kwargs):
        return {
            "total_fields": 20,
            "validated": 19,
            "partially_supported": 0,
            "unsupported": 0,
            "conflicting": 1,
            "not_found": 0,
            "needs_review": 0,
            "high_confidence": 19,
            "medium_confidence": 0,
            "low_confidence": 1,
            "unavailable_confidence": 0,
            "critical_unresolved_conflicts": ["Due Date"],
            "fields_recommended_for_review": ["Due Date"],
        }


def _value(value):
    return SimpleNamespace(value=value)


def _fixture(*, context=None, addendum_error=None, validation_error=None):
    bid_id, extraction_id, addendum_id, validation_id, profile_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    context = context or BidWorkflowContext(True, [uuid4()], [uuid4()], [])
    store = Store(context)
    extraction = Service(
        "extract",
        SimpleNamespace(
            run_id=extraction_id, status=_value("completed"), fields=Fields()
        ),
    )
    addendum = Service(
        "analyze",
        SimpleNamespace(
            run_id=addendum_id,
            status=_value("completed"),
            findings=[],
            addendum_documents=[object()],
            relationship_uncertain=False,
            ordering_uncertain=False,
            warnings=[],
        ),
        addendum_error,
    )
    validation = Service(
        "validate",
        SimpleNamespace(
            validation_run_id=validation_id,
            addendum_analysis_run_id=addendum_id,
            status=_value("completed"),
            overall_status=_value("needs_review"),
            addendum_check_status=_value("checked"),
            summary=Summary(),
            warnings=[],
        ),
        validation_error,
    )
    profile = Service(
        "build",
        SimpleNamespace(
            metadata=SimpleNamespace(
                extraction_run_id=extraction_id,
                validation_run_id=validation_id,
                profile_id=profile_id,
                status=_value("needs_review"),
                review_required=True,
                warnings=[],
            )
        ),
    )
    deps = WorkflowDependencies(
        store, store, extraction, addendum, validation, profile
    )
    return (
        bid_id,
        extraction_id,
        addendum_id,
        validation_id,
        profile_id,
        store,
        extraction,
        addendum,
        validation,
        profile,
        deps,
    )


def _run(fixture, checkpointer=None):
    bid_id = fixture[0]
    state = initial_state(uuid4(), bid_id, "test", datetime.now(UTC))
    graph = build_bid_intelligence_graph(fixture[-1], checkpointer)
    config = {"configurable": {"thread_id": str(state["workflow_id"])}}
    return asyncio.run(graph.ainvoke(state, config=config)), graph, config


def test_graph_compiles_with_required_nodes_and_executes_conditional_path():
    fixture = _fixture()
    result, graph, _ = _run(fixture)
    assert set(STAGES) <= set(graph.get_graph().nodes)
    assert result["workflow_status"] == "needs_review"
    assert all(
        status == "completed"
        for name, status in result["stage_statuses"].items()
        if not name.endswith("_agent")
    )
    assert all(
        result["stage_statuses"][name] == "skipped"
        for name in STAGES
        if name.endswith("_agent")
    )
    assert result["extraction_run_id"] == fixture[1]
    assert result["addendum_analysis_run_id"] == fixture[2]
    assert result["validation_run_id"] == fixture[3]
    assert result["profile_id"] == fixture[4]
    assert result["requires_human_review"] is True


def test_invalid_bid_context_routes_directly_to_failure_finalization():
    fixture = _fixture(context=BidWorkflowContext(False, [], [], []))
    result, _, _ = _run(fixture)
    assert result["workflow_status"] == "failed"
    assert result["stage_statuses"]["validate_bid_context"] == "failed"
    assert result["stage_statuses"]["finalize"] == "completed"
    assert fixture[6].calls == fixture[7].calls == []


def test_no_eligible_documents_routes_to_failure():
    fixture = _fixture(context=BidWorkflowContext(True, [uuid4()], [], []))
    result, _, _ = _run(fixture)
    assert result["workflow_status"] == "failed"
    assert result["error_code"] == "no_eligible_documents"


def test_extraction_failure_does_not_call_downstream_services():
    fixture = list(_fixture())
    fixture[6] = Service("extract", error=RuntimeError("provider unavailable"))
    fixture[-1] = WorkflowDependencies(
        fixture[5], fixture[5], fixture[6], fixture[7], fixture[8], fixture[9]
    )
    result, _, _ = _run(tuple(fixture))
    assert result["stage_statuses"]["extract_rfp_fields"] == "failed"
    assert fixture[7].calls == fixture[8].calls == fixture[9].calls == []


def test_no_addenda_is_explicitly_skipped_and_validation_continues():
    fixture = _fixture(addendum_error=NoAddendaAvailable("none"))
    result, _, _ = _run(fixture)
    assert result["stage_statuses"]["analyze_addenda"] == "skipped"
    assert result["addendum_summary"]["reason"] == "no_addenda_present"
    assert len(fixture[8].calls) == 1


def test_addendum_failure_routes_to_finalization_without_validation():
    fixture = _fixture(addendum_error=RuntimeError("comparison failed"))
    result, _, _ = _run(fixture)
    assert result["workflow_status"] == "failed"
    assert result["stage_statuses"]["analyze_addenda"] == "failed"
    assert fixture[8].calls == fixture[9].calls == []


def test_validation_failure_does_not_generate_profile():
    fixture = _fixture(validation_error=TimeoutError("validation timed out"))
    result, _, _ = _run(fixture)
    assert result["stage_statuses"]["validate_extraction"] == "failed"
    assert fixture[9].calls == []


def test_memory_checkpoint_uses_stable_thread_id_and_retains_final_state():
    saver = InMemorySaver()
    result, graph, config = _run(_fixture(), saver)
    snapshot = asyncio.run(graph.aget_state(config))
    assert snapshot.values["workflow_id"] == result["workflow_id"]
    assert snapshot.values["workflow_status"] == "needs_review"


def test_checkpointing_can_be_disabled_without_changing_graph_contract():
    config = Settings(_env_file=None, langgraph_checkpointing_enabled=False)
    assert get_checkpointer(config) is None
    result, _, _ = _run(_fixture(), checkpointer=None)
    assert result["workflow_status"] == "needs_review"


def test_unsupported_checkpointer_backend_is_rejected_by_configuration():
    with pytest.raises(ValueError, match="must be memory or none"):
        Settings(_env_file=None, langgraph_checkpointer_backend="redis")


class DomainAgent:
    def __init__(self, name, *, failed=False, review=False):
        self.name = name
        self.failed = failed
        self.review = review
        self.calls = []

    async def execute(self, request):
        self.calls.append(request)
        now = datetime.now(UTC)
        return SpecializedAgentResult(
            run_id=uuid4(),
            workflow_id=request.workflow_id,
            bid_id=request.bid_id,
            agent_name=self.name,
            status=(
                AgentExecutionStatus.FAILED
                if self.failed
                else AgentExecutionStatus.COMPLETED
            ),
            findings=[]
            if self.failed
            else [
                SpecializedFinding(
                    finding_id=f"{self.name.value}:one",
                    title="Finding",
                    description="Evidence-grounded finding",
                    category="test",
                    classification="explicit",
                    requires_human_review=self.review,
                )
            ],
            errors=["agent failed"] if self.failed else [],
            started_at=now,
            completed_at=now,
        )


class ReviewAgent:
    def __init__(self):
        self.calls = []

    async def execute(self, request):
        self.calls.append(request)
        now = datetime.now(UTC)
        return EvidenceReviewResult(
            run_id=uuid4(),
            workflow_id=request.workflow_id,
            bid_id=request.bid_id,
            status=AgentExecutionStatus.COMPLETED,
            reviewed_finding_count=len(request.prior_findings),
            valid_citation_count=0,
            invalid_citation_count=0,
            started_at=now,
            completed_at=now,
        )


def _with_agents(fixture, *, failed_name=None, review_name=None):
    names = (
        SpecializedAgentName.REQUIREMENTS,
        SpecializedAgentName.PRODUCT_SPECIFICATION,
        SpecializedAgentName.COMMERCIAL_TERMS,
        SpecializedAgentName.COMPLIANCE_DOCUMENTATION,
    )
    agents = {
        name: DomainAgent(
            name,
            failed=name == failed_name,
            review=name == review_name,
        )
        for name in names
    }
    review = ReviewAgent()
    fixture = list(fixture)
    fixture[-1] = replace(
        fixture[-1],
        requirements_agent=agents[SpecializedAgentName.REQUIREMENTS],
        product_agent=agents[SpecializedAgentName.PRODUCT_SPECIFICATION],
        commercial_agent=agents[SpecializedAgentName.COMMERCIAL_TERMS],
        compliance_agent=agents[SpecializedAgentName.COMPLIANCE_DOCUMENTATION],
        evidence_review_agent=review,
        specialized_agents_enabled=True,
    )
    return tuple(fixture), agents, review


def test_all_specialized_agent_nodes_update_state_before_validation():
    fixture, agents, review = _with_agents(_fixture())
    result, graph, _ = _run(fixture)
    expected_nodes = {
        "requirements_agent",
        "product_specification_agent",
        "commercial_terms_agent",
        "compliance_documentation_agent",
        "evidence_review_agent",
    }
    assert expected_nodes <= set(graph.get_graph().nodes)
    assert all(value == "completed" for value in result["agent_statuses"].values())
    assert len(result["requirements_findings"]) == 1
    assert len(result["product_findings"]) == 1
    assert len(result["commercial_findings"]) == 1
    assert len(result["compliance_findings"]) == 1
    assert len(review.calls[0].prior_findings) == 4
    assert len(fixture[8].calls) == 1


def test_required_specialized_agent_failure_stops_remaining_graph():
    fixture, agents, review = _with_agents(
        _fixture(), failed_name=SpecializedAgentName.PRODUCT_SPECIFICATION
    )
    result, _, _ = _run(fixture)
    assert result["workflow_status"] == "failed"
    assert result["agent_statuses"]["product_specification"] == "failed"
    assert agents[SpecializedAgentName.COMMERCIAL_TERMS].calls == []
    assert review.calls == []
    assert fixture[8].calls == []


def test_specialized_human_review_flag_survives_profile_generation():
    fixture, _, _ = _with_agents(
        _fixture(), review_name=SpecializedAgentName.REQUIREMENTS
    )
    result, _, _ = _run(fixture)
    assert result["profile_id"] is not None
    assert result["requires_human_review"] is True
    assert result["workflow_status"] == "needs_review"


class RecordingObserver:
    def __init__(self):
        self.observations = []

    async def record(self, observation):
        self.observations.append(observation)


def test_compiled_langgraph_emits_one_observation_per_executed_node():
    fixture = list(_fixture())
    observer = RecordingObserver()
    fixture[-1] = replace(fixture[-1], observer=observer)
    result, _, _ = _run(tuple(fixture))
    executed = [
        name
        for name, status in result["stage_statuses"].items()
        if status != "pending"
    ]
    assert [item.node_name for item in observer.observations] == executed
    assert all(item.operation_id == result["workflow_id"] for item in observer.observations)
    assert all(item.duration_ms >= 0 for item in observer.observations)
