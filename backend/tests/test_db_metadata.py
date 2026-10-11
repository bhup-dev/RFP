"""Model metadata tests. These do not connect to PostgreSQL."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

from app.db.base import Base
from app.db.models import (
    AddendumAnalysisRun,
    AddendumChange,
    Bid,
    BidComparisonProfile,
    BidComparisonRun,
    BidWorkflowRun,
    Document,
    DocumentChunk,
    ExtractedField,
    ExtractionRun,
    FieldValidationRecord,
    HumanReviewDecision,
    HumanReviewItem,
    ProcessingJob,
    SpecializedAgentFinding,
    SpecializedAgentRun,
    ValidationRun,
    WorkflowNodeEvent,
)
from app.main import app

EXPECTED_TABLES = {
    "bids",
    "documents",
    "document_chunks",
    "extraction_runs",
    "extracted_fields",
    "addendum_changes",
    "addendum_analysis_runs",
    "validation_runs",
    "field_validation_results",
    "bid_workflow_runs",
    "specialized_agent_runs",
    "specialized_agent_findings",
    "bid_comparison_runs",
    "bid_comparison_profiles",
    "workflow_node_events",
    "human_review_items",
    "human_review_decisions",
    "processing_jobs",
}

JSONB_COLUMNS = {
    ("document_chunks", "metadata"),
    ("extracted_fields", "value"),
    ("addendum_changes", "original_value"),
    ("addendum_changes", "amended_value"),
    ("addendum_changes", "sources"),
    ("addendum_analysis_runs", "original_document_ids"),
    ("addendum_analysis_runs", "addendum_document_ids"),
    ("validation_runs", "thresholds"),
    ("field_validation_results", "extracted_value"),
    ("field_validation_results", "supporting_citations"),
    ("field_validation_results", "contradicting_citations"),
    ("field_validation_results", "reasons"),
    ("field_validation_results", "deterministic_checks"),
    ("bid_workflow_runs", "stage_statuses"),
    ("bid_workflow_runs", "document_ids"),
    ("bid_workflow_runs", "summaries"),
    ("bid_workflow_runs", "warnings"),
    ("bid_workflow_runs", "errors"),
    ("specialized_agent_runs", "warnings"),
    ("specialized_agent_runs", "errors"),
    ("specialized_agent_runs", "review_issues"),
    ("specialized_agent_findings", "attributes"),
    ("specialized_agent_findings", "citations"),
    ("specialized_agent_findings", "unresolved_questions"),
    ("bid_comparison_runs", "dimensions"),
    ("bid_comparison_runs", "request_config"),
    ("bid_comparison_runs", "result"),
    ("bid_comparison_runs", "warnings"),
    ("bid_comparison_runs", "errors"),
    ("workflow_node_events", "run_references"),
    ("human_review_items", "proposed_value"),
    ("human_review_items", "supporting_citations"),
    ("human_review_items", "contradicting_citations"),
    ("human_review_items", "corrected_value"),
    ("human_review_decisions", "corrected_value"),
    ("human_review_decisions", "result_reference"),
    ("processing_jobs", "result_summary"),
}


def test_models_share_metadata() -> None:
    configure_mappers()
    models = [
        Bid,
        Document,
        DocumentChunk,
        ExtractionRun,
        ExtractedField,
        AddendumAnalysisRun,
        AddendumChange,
        ValidationRun,
        FieldValidationRecord,
        BidWorkflowRun,
        SpecializedAgentRun,
        SpecializedAgentFinding,
        BidComparisonRun,
        BidComparisonProfile,
        WorkflowNodeEvent,
        HumanReviewItem,
        HumanReviewDecision,
        ProcessingJob,
    ]
    assert {model.__tablename__ for model in models} == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    for model in models:
        assert model.metadata is Base.metadata


def test_foreign_keys_and_delete_behavior() -> None:
    expected = {
        ("documents", "bid_id"): "bids.id",
        ("document_chunks", "document_id"): "documents.id",
        ("extraction_runs", "bid_id"): "bids.id",
        ("extracted_fields", "extraction_run_id"): "extraction_runs.id",
        ("addendum_changes", "bid_id"): "bids.id",
        ("addendum_analysis_runs", "bid_id"): "bids.id",
        ("addendum_changes", "analysis_run_id"): "addendum_analysis_runs.id",
        ("validation_runs", "bid_id"): "bids.id",
        ("validation_runs", "extraction_run_id"): "extraction_runs.id",
        ("field_validation_results", "validation_run_id"): "validation_runs.id",
        ("bid_workflow_runs", "bid_id"): "bids.id",
        ("specialized_agent_runs", "workflow_id"): "bid_workflow_runs.id",
        ("specialized_agent_runs", "bid_id"): "bids.id",
        ("specialized_agent_findings", "agent_run_id"): "specialized_agent_runs.id",
        ("specialized_agent_findings", "bid_id"): "bids.id",
        ("bid_comparison_profiles", "comparison_id"): "bid_comparison_runs.id",
        ("bid_comparison_profiles", "bid_id"): "bids.id",
        ("human_review_items", "bid_id"): "bids.id",
        ("human_review_decisions", "review_item_id"): "human_review_items.id",
    }
    for (table_name, column_name), target in expected.items():
        column = Base.metadata.tables[table_name].c[column_name]
        foreign_keys = list(column.foreign_keys)
        assert len(foreign_keys) == 1
        assert foreign_keys[0].target_fullname == target
        assert foreign_keys[0].ondelete == "CASCADE"
    workflow_references = {
        "extraction_run_id": "extraction_runs.id",
        "addendum_analysis_run_id": "addendum_analysis_runs.id",
        "validation_run_id": "validation_runs.id",
    }
    for column_name, target in workflow_references.items():
        foreign_key = next(
            iter(Base.metadata.tables["bid_workflow_runs"].c[column_name].foreign_keys)
        )
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "SET NULL"
    comparison_references = {
        "extraction_run_id": "extraction_runs.id",
        "addendum_analysis_run_id": "addendum_analysis_runs.id",
        "validation_run_id": "validation_runs.id",
    }
    for column_name, target in comparison_references.items():
        foreign_key = next(
            iter(Base.metadata.tables["bid_comparison_profiles"].c[column_name].foreign_keys)
        )
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "SET NULL"
    for column_name in ("original_document_id", "addendum_document_id"):
        foreign_key = next(
            iter(Base.metadata.tables["addendum_changes"].c[column_name].foreign_keys)
        )
        assert foreign_key.target_fullname == "documents.id"
        assert foreign_key.ondelete == "SET NULL"


def test_unique_constraints() -> None:
    assert ("bid_identifier",) in _unique_columns("bids")
    assert ("document_id", "chunk_index") in _unique_columns("document_chunks")
    assert ("extraction_run_id", "field_name") in _unique_columns("extracted_fields")
    assert ("validation_run_id", "field_name") in _unique_columns(
        "field_validation_results"
    )
    assert ("workflow_id", "agent_name") in _unique_columns(
        "specialized_agent_runs"
    )
    assert ("agent_run_id", "finding_key") in _unique_columns(
        "specialized_agent_findings"
    )
    assert ("comparison_id", "bid_id") in _unique_columns(
        "bid_comparison_profiles"
    )
    assert ("workflow_id", "item_reference") in _unique_columns(
        "human_review_items"
    )
    assert ("review_item_id", "idempotency_key") in _unique_columns(
        "human_review_decisions"
    )


def test_foreign_key_indexes() -> None:
    assert "bid_id" in _indexed_columns("documents")
    assert "bid_id" in _indexed_columns("extraction_runs")
    assert "bid_id" in _indexed_columns("addendum_changes")
    assert "bid_id" in _indexed_columns("addendum_analysis_runs")
    assert "analysis_run_id" in _indexed_columns("addendum_changes")
    assert "bid_id" in _indexed_columns("validation_runs")
    assert "extraction_run_id" in _indexed_columns("validation_runs")
    assert "validation_run_id" in _indexed_columns("field_validation_results")
    assert "bid_id" in _indexed_columns("bid_workflow_runs")
    assert "extraction_run_id" in _indexed_columns("bid_workflow_runs")
    assert "addendum_analysis_run_id" in _indexed_columns("bid_workflow_runs")
    assert "validation_run_id" in _indexed_columns("bid_workflow_runs")
    assert "workflow_id" in _indexed_columns("specialized_agent_runs")
    assert "bid_id" in _indexed_columns("specialized_agent_runs")
    assert "agent_run_id" in _indexed_columns("specialized_agent_findings")
    assert "bid_id" in _indexed_columns("specialized_agent_findings")
    assert "comparison_id" in _indexed_columns("bid_comparison_profiles")
    assert "bid_id" in _indexed_columns("bid_comparison_profiles")
    assert "operation_type" in _indexed_columns("workflow_node_events")
    assert "operation_id" in _indexed_columns("workflow_node_events")
    assert "bid_id" in _indexed_columns("workflow_node_events")
    assert "node_name" in _indexed_columns("workflow_node_events")
    assert "status" in _indexed_columns("workflow_node_events")
    assert "bid_id" in _indexed_columns("human_review_items")
    assert "workflow_id" in _indexed_columns("human_review_items")
    assert "review_item_id" in _indexed_columns("human_review_decisions")
    assert "document_id" in _indexed_columns("document_chunks")


def test_relationships() -> None:
    configure_mappers()
    assert Bid.documents.property.mapper.class_ is Document
    assert Document.bid.property.mapper.class_ is Bid
    assert Document.chunks.property.mapper.class_ is DocumentChunk
    assert DocumentChunk.document.property.mapper.class_ is Document
    assert Bid.extraction_runs.property.mapper.class_ is ExtractionRun
    assert ExtractionRun.extracted_fields.property.mapper.class_ is ExtractedField
    assert ExtractedField.extraction_run.property.mapper.class_ is ExtractionRun
    assert Bid.addendum_changes.property.mapper.class_ is AddendumChange
    assert AddendumChange.bid.property.mapper.class_ is Bid
    assert Bid.addendum_analysis_runs.property.mapper.class_ is AddendumAnalysisRun
    assert AddendumAnalysisRun.bid.property.mapper.class_ is Bid
    assert AddendumAnalysisRun.findings.property.mapper.class_ is AddendumChange
    assert AddendumChange.analysis_run.property.mapper.class_ is AddendumAnalysisRun
    assert Bid.validation_runs.property.mapper.class_ is ValidationRun
    assert ValidationRun.bid.property.mapper.class_ is Bid
    assert ExtractionRun.validation_runs.property.mapper.class_ is ValidationRun
    assert ValidationRun.extraction_run.property.mapper.class_ is ExtractionRun
    assert ValidationRun.field_results.property.mapper.class_ is FieldValidationRecord
    assert FieldValidationRecord.validation_run.property.mapper.class_ is ValidationRun
    assert Bid.workflow_runs.property.mapper.class_ is BidWorkflowRun
    assert BidWorkflowRun.bid.property.mapper.class_ is Bid
    assert SpecializedAgentRun.workflow.property.mapper.class_ is BidWorkflowRun
    assert (
        SpecializedAgentRun.findings.property.mapper.class_
        is SpecializedAgentFinding
    )
    assert SpecializedAgentFinding.agent_run.property.mapper.class_ is SpecializedAgentRun
    assert BidComparisonRun.profiles.property.mapper.class_ is BidComparisonProfile
    assert BidComparisonProfile.comparison.property.mapper.class_ is BidComparisonRun
    assert HumanReviewItem.decisions.property.mapper.class_ is HumanReviewDecision
    assert HumanReviewDecision.review_item.property.mapper.class_ is HumanReviewItem


def test_timestamps_are_timezone_aware() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True


def test_jsonb_columns_and_no_embeddings() -> None:
    for table_name, column_name in JSONB_COLUMNS:
        column = Base.metadata.tables[table_name].c[column_name]
        assert isinstance(column.type, JSONB)
    chunk_columns = set(Base.metadata.tables["document_chunks"].columns.keys())
    assert "metadata" in chunk_columns
    assert not any("embed" in name or "vector" in name for name in chunk_columns)


def test_health_check_does_not_open_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.session as session

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("database engine was created")

    monkeypatch.setattr(session, "create_async_engine", fail_if_called)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert session._engine is None


def test_alembic_offline_migration_renders_schema() -> None:
    import subprocess
    import sys

    backend_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout.lower()
    for table_name in EXPECTED_TABLES:
        assert f"create table {table_name}" in sql
    for path in (backend_root / "alembic" / "versions").glob("*.py"):
        migration = path.read_text()
        assert "postgresql+asyncpg://" not in migration
        assert "postgres:postgres" not in migration


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    found: set[tuple[str, ...]] = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            found.add(tuple(column.name for column in constraint.columns))
    return found


def _indexed_columns(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]
    names: set[str] = set()
    for index in table.indexes:
        names.update(column.name for column in index.columns)
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.columns:
            names.add(next(iter(constraint.columns)).name)
    return names
