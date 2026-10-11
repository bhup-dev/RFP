"""Deterministic mapping from validated workflow state to review candidates."""

from typing import Any
from uuid import UUID

from app.agents.state import BidIntelligenceState
from app.agents.comparison_state import BidComparisonState
from app.schemas.review import ReviewCategory, ReviewSeverity


def review_candidates(
    state: BidIntelligenceState,
    *,
    confidence_threshold: float,
    critical_fields: set[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for field in state["validation_findings"]:
        name = str(field["field_name"])
        status = str(field["validation_status"])
        confidence = field.get("confidence_score")
        if status in {"not_found", "not_stated"} and name not in critical_fields:
            continue
        category = _field_category(name, status)
        low_confidence = (
            confidence is not None and float(confidence) < confidence_threshold
        )
        if (
            status
            not in {
                "partially_supported",
                "unsupported",
                "conflicting",
                "needs_review",
                "not_found",
                "not_stated",
            }
            and not low_confidence
        ):
            continue
        reason = "; ".join(field.get("validation_reasons") or [])
        if low_confidence:
            reason = (
                f"{reason}; " if reason else ""
            ) + f"confidence is below {confidence_threshold:.2f}"
        candidates.append(
            {
                "source_stage": "validate_extraction",
                "category": category.value,
                "item_reference": f"validation:{name}",
                "field_name": name,
                "proposed_value": field.get("extracted_value"),
                "validation_status": status,
                "confidence_score": confidence,
                "supporting_citations": field.get("supporting_citations") or [],
                "contradicting_citations": field.get("contradicting_citations")
                or [],
                "reason": reason or f"{name} requires human verification",
                "severity": _severity(name, status, critical_fields).value,
            }
        )

    findings = {
        item["finding_id"]: item
        for key in (
            "requirements_findings",
            "product_findings",
            "commercial_findings",
            "compliance_findings",
        )
        for item in state[key]
    }
    for issue in state["evidence_review_findings"]:
        finding_id = str(issue["finding_id"])
        finding = findings.get(finding_id, {})
        issue_type = str(issue["issue_type"])
        category = {
            "invalid_citation": ReviewCategory.INVALID_CITATION,
            "cross_bid_citation": ReviewCategory.INVALID_CITATION,
            "missing_evidence": ReviewCategory.UNSUPPORTED_CLAIM,
            "conflicting_findings": ReviewCategory.INCONSISTENT_AGENT_FINDINGS,
        }.get(issue_type, ReviewCategory.OTHER)
        candidates.append(
            {
                "source_stage": f"{issue.get('agent_name', 'evidence_review')}_agent",
                "category": category.value,
                "item_reference": f"agent:{finding_id}:{issue_type}",
                "field_name": finding.get("category"),
                "proposed_value": finding,
                "validation_status": issue_type,
                "confidence_score": finding.get("confidence"),
                "supporting_citations": finding.get("citations") or [],
                "contradicting_citations": [],
                "reason": str(issue["message"]),
                "severity": (
                    ReviewSeverity.HIGH.value
                    if category
                    in {
                        ReviewCategory.INVALID_CITATION,
                        ReviewCategory.INCONSISTENT_AGENT_FINDINGS,
                    }
                    else ReviewSeverity.MEDIUM.value
                ),
            }
        )
    unique = {item["item_reference"]: item for item in candidates}
    return list(unique.values())


def comparison_review_candidates(
    state: BidComparisonState,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw_row in state["comparison_rows"]:
        classification = str(raw_row["classification"])
        if not raw_row.get("requires_human_review") and classification not in {
            "uncertain",
            "conflicting",
            "not_comparable",
        }:
            continue
        attribute = str(raw_row["attribute"])
        for value in raw_row["values"]:
            bid_id = UUID(str(value["bid_id"]))
            candidates.append(
                {
                    "bid_id": bid_id,
                    "source_stage": "validate_comparison_evidence",
                    "category": ReviewCategory.COMPARISON_UNCERTAINTY.value,
                    "item_reference": f"comparison:{attribute}:{bid_id}",
                    "field_name": attribute,
                    "proposed_value": value.get("original_value"),
                    "validation_status": classification,
                    "confidence_score": raw_row.get("confidence"),
                    "supporting_citations": value.get("supporting_citations") or [],
                    "contradicting_citations": value.get("contradicting_citations")
                    or [],
                    "reason": str(
                        raw_row.get("review_recommendation")
                        or raw_row.get("explanation")
                        or "comparison conclusion requires review"
                    ),
                    "severity": (
                        ReviewSeverity.HIGH.value
                        if attribute
                        in {
                            "Due Date",
                            "Delivery Date",
                            "Model_no",
                            "Part_no",
                        }
                        or classification == "conflicting"
                        else ReviewSeverity.MEDIUM.value
                    ),
                }
            )
    return candidates


def _field_category(name: str, status: str) -> ReviewCategory:
    if status == "unsupported":
        return ReviewCategory.UNSUPPORTED_CLAIM
    if status == "conflicting":
        return (
            ReviewCategory.AMBIGUOUS_DEADLINE
            if name in {"Due Date", "Delivery Date", "Pre Bid Meeting"}
            else ReviewCategory.CONFLICTING_REQUIREMENT
        )
    if name in {"Product", "Model_no", "Part_no", "Product Specification"}:
        return ReviewCategory.UNCERTAIN_PRODUCT_SPECIFICATION
    if status in {"not_found", "not_stated"}:
        return ReviewCategory.INCOMPLETE_PROFILE
    return ReviewCategory.OTHER


def _severity(
    name: str, status: str, critical_fields: set[str]
) -> ReviewSeverity:
    if name in critical_fields and status in {
        "conflicting",
        "unsupported",
        "not_found",
        "not_stated",
    }:
        return ReviewSeverity.CRITICAL
    if status in {"conflicting", "unsupported"}:
        return ReviewSeverity.HIGH
    return ReviewSeverity.MEDIUM
