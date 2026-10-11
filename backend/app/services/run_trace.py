"""Assemble a read-only trace of one bid workflow run from stored records.

Prompts and raw model responses are never persisted, so a trace points to the code that
defines each prompt instead of reproducing it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.addendum import AddendumAnalysisRun, AddendumChange
from app.db.models.extraction import ExtractedField, ExtractionRun
from app.db.models.observability import WorkflowNodeEvent
from app.db.models.orchestration import BidWorkflowRun
from app.db.models.review import HumanReviewItem
from app.db.models.specialized_agent import SpecializedAgentFinding, SpecializedAgentRun
from app.db.models.validation import FieldValidationRecord, ValidationRun

PROMPT_LOCATIONS: dict[str, str] = {
    "extract_rfp_fields": "backend/app/services/extraction_service.py (SYSTEM_PROMPT)",
    "analyze_addenda": (
        "backend/app/services/addendum_analysis_service.py "
        "(DISCOVERY_SYSTEM_PROMPT, COMPARISON_SYSTEM_PROMPT)"
    ),
    "requirements_agent": "backend/app/agents/specialized.py (AGENT_DEFINITIONS[REQUIREMENTS])",
    "product_specification_agent": (
        "backend/app/agents/specialized.py (AGENT_DEFINITIONS[PRODUCT_SPECIFICATION])"
    ),
    "commercial_terms_agent": "backend/app/agents/specialized.py (AGENT_DEFINITIONS[COMMERCIAL_TERMS])",
    "compliance_documentation_agent": (
        "backend/app/agents/specialized.py (AGENT_DEFINITIONS[COMPLIANCE_DOCUMENTATION])"
    ),
    "validate_extraction": (
        "backend/app/services/extraction_validation_service.py (SEMANTIC_SYSTEM_PROMPT)"
    ),
}
NO_MODEL_NODES = frozenset(
    {
        "validate_bid_context",
        "evidence_review_agent",
        "create_review_items",
        "human_review_gate",
        "generate_profile",
        "finalize",
    }
)


class TraceNotFound(Exception):
    """The bid has no workflow run to trace."""


async def build_run_trace(
    session: AsyncSession, bid_id: UUID, workflow_id: UUID | None = None
) -> dict[str, Any]:
    run = await _workflow_run(session, bid_id, workflow_id)
    events = (
        await session.scalars(
            select(WorkflowNodeEvent)
            .where(WorkflowNodeEvent.operation_id == run.id)
            .order_by(WorkflowNodeEvent.started_at)
        )
    ).all()
    return {
        "trace_type": "bid_workflow",
        "note": (
            "Built from stored records only. Prompts and raw model responses are not "
            "persisted; prompt_location names the code that defines each prompt."
        ),
        "workflow": {
            "workflow_id": run.id,
            "bid_id": run.bid_id,
            "workflow_version": run.workflow_version,
            "status": run.status,
            "current_stage": run.current_stage,
            "stage_statuses": run.stage_statuses,
            "document_ids": run.document_ids,
            "eligible_document_count": run.eligible_document_count,
            "requires_human_review": run.requires_human_review,
            "summaries": run.summaries,
            "warnings": run.warnings,
            "errors": run.errors,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        },
        "steps": [_event(event) for event in events],
        "extraction": await _extraction(session, run.extraction_run_id),
        "addendum_analysis": await _addendum(session, run.addendum_analysis_run_id),
        "specialized_agents": await _agents(session, run.id),
        "validation": await _validation(session, run.validation_run_id),
        "review_items": await _reviews(session, run.id),
        "profile_id": run.profile_id,
    }


async def _workflow_run(
    session: AsyncSession, bid_id: UUID, workflow_id: UUID | None
) -> BidWorkflowRun:
    query = select(BidWorkflowRun).where(BidWorkflowRun.bid_id == bid_id)
    if workflow_id is not None:
        query = query.where(BidWorkflowRun.id == workflow_id)
    else:
        query = query.where(BidWorkflowRun.extraction_run_id.is_not(None))
    run = await session.scalar(query.order_by(BidWorkflowRun.started_at.desc()).limit(1))
    if run is None:
        raise TraceNotFound("no workflow run with an extraction was found for this bid")
    return run


def _event(event: WorkflowNodeEvent) -> dict[str, Any]:
    return {
        "node": event.node_name,
        "status": event.status,
        "started_at": event.started_at,
        "completed_at": event.completed_at,
        "duration_ms": event.duration_ms,
        "retry_count": event.retry_count,
        "error_category": event.error_category,
        "error_detail": event.error_detail,
        "run_references": event.run_references,
        "requires_human_review": event.requires_human_review,
        "calls_model": event.node_name not in NO_MODEL_NODES,
        "prompt_location": PROMPT_LOCATIONS.get(event.node_name),
    }


async def _extraction(session: AsyncSession, run_id: UUID | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run = await session.get(ExtractionRun, run_id)
    if run is None:
        return None
    fields = (
        await session.scalars(
            select(ExtractedField)
            .where(ExtractedField.extraction_run_id == run_id)
            .order_by(ExtractedField.created_at)
        )
    ).all()
    return {
        "extraction_run_id": run.id,
        "status": run.status,
        "model_name": run.model_name,
        "document_ids": run.document_ids,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_message": run.error_message,
        "fields": [
            {
                "field_name": field.field_name,
                "value": field.value,
                "confidence": field.confidence,
                "validation_status": field.validation_status,
                "notes": field.notes,
                "citations": field.citations,
            }
            for field in fields
        ],
    }


async def _addendum(session: AsyncSession, run_id: UUID | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run = await session.get(AddendumAnalysisRun, run_id)
    if run is None:
        return None
    findings = (
        await session.scalars(
            select(AddendumChange)
            .where(AddendumChange.analysis_run_id == run_id)
            .order_by(AddendumChange.created_at)
        )
    ).all()
    return {
        "addendum_analysis_run_id": run.id,
        "status": run.status,
        "model_name": run.model_name,
        "original_document_ids": run.original_document_ids,
        "addendum_document_ids": run.addendum_document_ids,
        "relationship_uncertain": run.relationship_uncertain,
        "ordering_uncertain": run.ordering_uncertain,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_message": run.error_message,
        "findings": [
            {
                "field_name": row.field_name,
                "status": row.status,
                "original_value": row.original_value,
                "amended_value": row.amended_value,
                "severity": row.severity,
                "confidence": row.confidence,
                "notes": row.notes,
                "impact_note": row.impact_note,
                "sources": row.sources,
            }
            for row in findings
        ],
    }


async def _agents(session: AsyncSession, workflow_id: UUID) -> list[dict[str, Any]]:
    runs = (
        await session.scalars(
            select(SpecializedAgentRun)
            .where(SpecializedAgentRun.workflow_id == workflow_id)
            .order_by(SpecializedAgentRun.started_at)
        )
    ).all()
    result = []
    for run in runs:
        findings = (
            await session.scalars(
                select(SpecializedAgentFinding)
                .where(SpecializedAgentFinding.agent_run_id == run.id)
                .order_by(SpecializedAgentFinding.created_at)
            )
        ).all()
        result.append(
            {
                "agent_run_id": run.id,
                "agent_name": run.agent_name,
                "status": run.status,
                "model_name": run.model_name,
                "evidence_chunk_count": run.evidence_chunk_count,
                "warnings": run.warnings,
                "errors": run.errors,
                "review_issues": run.review_issues,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "findings": [
                    {
                        "finding_key": item.finding_key,
                        "title": item.title,
                        "description": item.description,
                        "category": item.category,
                        "classification": item.classification,
                        "attributes": item.attributes,
                        "confidence": item.confidence,
                        "citations": item.citations,
                        "unresolved_questions": item.unresolved_questions,
                        "requires_human_review": item.requires_human_review,
                    }
                    for item in findings
                ],
            }
        )
    return result


async def _validation(session: AsyncSession, run_id: UUID | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run = await session.get(ValidationRun, run_id)
    if run is None:
        return None
    records = (
        await session.scalars(
            select(FieldValidationRecord)
            .where(FieldValidationRecord.validation_run_id == run_id)
            .order_by(FieldValidationRecord.created_at)
        )
    ).all()
    return {
        "validation_run_id": run.id,
        "status": run.status,
        "overall_status": run.overall_status,
        "model_name": run.model_name,
        "thresholds": run.thresholds,
        "addendum_check_status": run.addendum_check_status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_message": run.error_message,
        "fields": [
            {
                "field_name": record.field_name,
                "extracted_value": record.extracted_value,
                "status": record.status,
                "confidence_score": record.confidence_score,
                "confidence_category": record.confidence_category,
                "reasons": record.reasons,
                "deterministic_checks": record.deterministic_checks,
                "recommended_action": record.recommended_action,
                "supporting_citations": record.supporting_citations,
                "contradicting_citations": record.contradicting_citations,
            }
            for record in records
        ],
    }


async def _reviews(session: AsyncSession, workflow_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(HumanReviewItem)
            .where(HumanReviewItem.workflow_id == workflow_id)
            .order_by(HumanReviewItem.created_at)
        )
    ).all()
    return [
        {
            "review_item_id": row.id,
            "source_stage": row.source_stage,
            "category": row.category,
            "severity": row.severity,
            "status": row.status,
            "field_name": row.field_name,
            "proposed_value": row.proposed_value,
            "validation_status": row.validation_status,
            "reason": row.reason,
        }
        for row in rows
    ]
