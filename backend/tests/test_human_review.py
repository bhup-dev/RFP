import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.state import initial_state
from app.agents.workflow import build_bid_intelligence_graph
from app.api.reviews import require_reviewer
from app.config import settings
from app.main import app
from app.schemas.review import ReviewDecisionRequest
from app.services.review_guardrails import (
    comparison_review_candidates,
    review_candidates,
)
from test_langgraph_workflow import _fixture


def _review_state():
    state = initial_state(uuid4(), uuid4(), "test", datetime.now(UTC))
    state["validation_findings"] = [
        {
            "field_name": "Due Date",
            "extracted_value": "June 1 or June 2",
            "validation_status": "conflicting",
            "confidence_score": 0.99,
            "supporting_citations": [],
            "contradicting_citations": [],
            "validation_reasons": ["original and addendum conflict"],
        },
        {
            "field_name": "Optional Notes",
            "extracted_value": None,
            "validation_status": "not_found",
            "confidence_score": None,
            "supporting_citations": [],
            "contradicting_citations": [],
            "validation_reasons": [],
        },
    ]
    return state


def test_critical_conflict_routes_even_with_high_confidence_and_optional_missing_does_not():
    items = review_candidates(
        _review_state(),
        confidence_threshold=0.55,
        critical_fields={"Due Date"},
    )
    assert len(items) == 1
    assert items[0]["category"] == "ambiguous_deadline"
    assert items[0]["severity"] == "critical"


def test_not_stated_creates_a_review_item_only_for_critical_fields():
    state = _review_state()
    state["validation_findings"] = [
        {
            "field_name": name,
            "extracted_value": "Not specified in the bid documents",
            "validation_status": "not_stated",
            "confidence_score": None,
            "supporting_citations": [],
            "contradicting_citations": [],
            "validation_reasons": ["no statement found"],
        }
        for name in ("Due Date", "Bid Bond Requirement")
    ]
    items = review_candidates(state, confidence_threshold=0.55, critical_fields={"Due Date"})
    assert [item["field_name"] for item in items] == ["Due Date"]
    assert items[0]["category"] == "incomplete_profile"
    assert items[0]["severity"] == "critical"


def test_invalid_agent_citation_is_always_reviewed():
    state = _review_state()
    state["validation_findings"] = []
    finding_id = uuid4()
    state["requirements_findings"] = [
        {
            "finding_id": str(finding_id),
            "category": "requirement",
            "summary": "Unsupported",
            "details": {},
            "confidence": 1.0,
            "citations": [],
            "requires_human_review": False,
        }
    ]
    state["evidence_review_findings"] = [
        {
            "finding_id": str(finding_id),
            "agent_name": "requirements",
            "issue_type": "cross_bid_citation",
            "message": "citation belongs to another bid",
        }
    ]
    items = review_candidates(
        state, confidence_threshold=0.55, critical_fields=set()
    )
    assert items[0]["category"] == "invalid_citation"
    assert items[0]["severity"] == "high"


def test_uncertain_comparison_creates_one_review_item_per_bid():
    bid_ids = [uuid4(), uuid4()]
    items = comparison_review_candidates(
        {
            "comparison_rows": [
                {
                    "attribute": "Model_no",
                    "classification": "uncertain",
                    "confidence": 0.4,
                    "requires_human_review": True,
                    "review_recommendation": "Confirm model equivalence.",
                    "values": [
                        {
                            "bid_id": bid_id,
                            "original_value": f"model-{index}",
                            "supporting_citations": [],
                            "contradicting_citations": [],
                        }
                        for index, bid_id in enumerate(bid_ids)
                    ],
                }
            ]
        }
    )
    assert {item["bid_id"] for item in items} == set(bid_ids)
    assert all(item["category"] == "comparison_uncertainty" for item in items)


def test_decision_schema_requires_comments_and_idempotency_key():
    with pytest.raises(ValidationError):
        ReviewDecisionRequest(
            decision="reject", idempotency_key=uuid4(), comments=None
        )
    with pytest.raises(ValidationError):
        ReviewDecisionRequest(
            decision="approve",
            idempotency_key=uuid4(),
            corrected_value="corrected",
        )


def test_reviewer_boundary_fails_closed_when_token_is_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "human_review_api_token", None)
    with pytest.raises(HTTPException) as exc:
        require_reviewer(authorization=None, reviewer_id="reviewer")
    assert exc.value.status_code == 503


def test_review_routes_are_schema_bound_and_unauthorized_calls_are_rejected(
    monkeypatch,
):
    paths = app.openapi()["paths"]
    assert "/api/v1/reviews" in paths
    assert "/api/v1/reviews/{review_id}" in paths
    assert "/api/v1/reviews/{review_id}/decision" in paths
    assert "/api/v1/workflows/{workflow_id}/review-status" in paths
    monkeypatch.setattr(settings, "human_review_api_token", "test-review-token")
    response = TestClient(app).get(
        "/api/v1/reviews",
        headers={
            "Authorization": "Bearer wrong-token",
            "X-Reviewer-ID": "reviewer",
        },
    )
    assert response.status_code == 403


class ReviewManager:
    def __init__(self):
        self.item_id = uuid4()
        self.resolved = False

    async def create_items(self, _state):
        return [self.item_id]

    async def all_resolved(self, _workflow_id):
        return self.resolved


def test_langgraph_interrupt_cannot_be_bypassed_and_resumes_after_persisted_resolution():
    fixture = _fixture()
    manager = ReviewManager()
    deps = replace(
        fixture[-1],
        review_manager=manager,
        human_review_interrupt_enabled=True,
    )
    saver = InMemorySaver()
    graph = build_bid_intelligence_graph(deps, saver)
    state = initial_state(uuid4(), fixture[0], "test", datetime.now(UTC))
    config = {"configurable": {"thread_id": str(state["workflow_id"])}}

    interrupted = asyncio.run(graph.ainvoke(state, config=config))
    assert interrupted["workflow_status"] == "pending_review"
    assert interrupted["review_item_ids"] == [manager.item_id]
    assert interrupted["__interrupt__"]

    still_interrupted = asyncio.run(
        graph.ainvoke(Command(resume={"reviewed": True}), config=config)
    )
    assert still_interrupted["__interrupt__"]
    assert still_interrupted["workflow_status"] == "pending_review"

    manager.resolved = True
    completed = asyncio.run(
        graph.ainvoke(Command(resume={"reviewed": True}), config=config)
    )
    assert completed["workflow_status"] == "needs_review"
    assert completed["review_status"] == "resolved"
    assert completed["profile_id"] is not None
