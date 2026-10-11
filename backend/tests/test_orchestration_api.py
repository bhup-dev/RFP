from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agents.workflow import WorkflowDependencies
from app.api.orchestration import get_workflow_dependencies
from app.main import app
from app.services.orchestration_store import BidWorkflowContext


class ApiStore:
    def __init__(self, exists=True):
        self.exists = exists
        self.state = None

    async def bid_exists(self, _bid_id):
        return self.exists

    async def create_run(self, state):
        self.state = state.copy()

    async def load_bid_context(self, _bid_id):
        return BidWorkflowContext(True, [uuid4()], [uuid4()], [])

    async def save_state(self, state):
        self.state = state.copy()

    async def load_state(self, _workflow_id):
        return self.state


class Fields:
    def model_dump(self, **_kwargs):
        return {"Bid Number": {"value": "SYN-API"}}


class Summary:
    def model_dump(self, **_kwargs):
        return {
            "total_fields": 20,
            "validated": 20,
            "partially_supported": 0,
            "unsupported": 0,
            "conflicting": 0,
            "not_found": 0,
            "needs_review": 0,
            "high_confidence": 20,
            "medium_confidence": 0,
            "low_confidence": 0,
            "unavailable_confidence": 0,
            "critical_unresolved_conflicts": [],
            "fields_recommended_for_review": [],
        }


class Extraction:
    def __init__(self):
        self.run_id = uuid4()

    async def extract(self, _bid_id):
        return SimpleNamespace(
            run_id=self.run_id,
            status=SimpleNamespace(value="completed"),
            fields=Fields(),
        )


class Addendum:
    def __init__(self):
        self.run_id = uuid4()

    async def analyze(self, _bid_id):
        return SimpleNamespace(
            run_id=self.run_id,
            status=SimpleNamespace(value="completed"),
            findings=[],
            addendum_documents=[],
            relationship_uncertain=False,
            ordering_uncertain=False,
            warnings=[],
        )


class Validation:
    def __init__(self, extraction, addendum):
        self.run_id = uuid4()
        self.extraction = extraction
        self.addendum = addendum

    async def validate(self, bid_id, extraction_run_id):
        assert extraction_run_id == self.extraction.run_id
        return SimpleNamespace(
            validation_run_id=self.run_id,
            addendum_analysis_run_id=self.addendum.run_id,
            status=SimpleNamespace(value="completed"),
            overall_status=SimpleNamespace(value="validated"),
            addendum_check_status=SimpleNamespace(value="checked"),
            summary=Summary(),
            warnings=[],
        )


class Profile:
    def __init__(self, extraction, validation):
        self.extraction = extraction
        self.validation = validation

    async def build(self, bid_id, extraction_run_id):
        assert extraction_run_id == self.extraction.run_id
        return SimpleNamespace(
            metadata=SimpleNamespace(
                extraction_run_id=extraction_run_id,
                validation_run_id=self.validation.run_id,
                profile_id=uuid4(),
                status=SimpleNamespace(value="ready"),
                review_required=False,
                warnings=[],
            )
        )


def _dependencies(exists=True):
    store = ApiStore(exists)
    extraction, addendum = Extraction(), Addendum()
    validation = Validation(extraction, addendum)
    return WorkflowDependencies(
        store,
        store,
        extraction,
        addendum,
        validation,
        Profile(extraction, validation),
    )


def test_workflow_endpoint_returns_real_graph_outcome_and_run_references():
    deps = _dependencies()
    app.dependency_overrides[get_workflow_dependencies] = lambda: deps
    try:
        response = TestClient(app).post(f"/api/v1/bids/{uuid4()}/workflow")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed_with_warnings"
    assert body["extraction_run_id"] == str(deps.extraction.run_id)
    assert body["validation_run_id"] == str(deps.validation.run_id)
    assert body["profile_status"] == "ready"
    assert set(body["agent_statuses"].values()) == {"skipped"}
    assert body["finding_counts"]["requirements"] == 0
    assert body["completed_at"] is not None


def test_workflow_endpoint_rejects_unknown_bid_before_creating_run():
    deps = _dependencies(exists=False)
    app.dependency_overrides[get_workflow_dependencies] = lambda: deps
    try:
        response = TestClient(app).post(f"/api/v1/bids/{uuid4()}/workflow")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert deps.workflow_store.state is None
