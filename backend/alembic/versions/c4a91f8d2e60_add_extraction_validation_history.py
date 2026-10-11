"""add extraction validation history

Revision ID: c4a91f8d2e60
Revises: 8d6a1c4e7b22
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4a91f8d2e60"
down_revision: Union[str, Sequence[str], None] = "8d6a1c4e7b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("addendum_analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("overall_status", sa.String(length=32), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("addendum_check_status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bid_id"], ["bids.id"], name="fk_validation_runs_bid_id_bids", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], name="fk_validation_runs_extraction_run_id_extraction_runs", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["addendum_analysis_run_id"], ["addendum_analysis_runs.id"], name="fk_validation_runs_addendum_run", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_validation_runs"),
    )
    op.create_index("ix_validation_runs_bid_id", "validation_runs", ["bid_id"])
    op.create_index("ix_validation_runs_extraction_run_id", "validation_runs", ["extraction_run_id"])
    op.create_table(
        "field_validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("extracted_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Numeric(8, 5), nullable=True),
        sa.Column("confidence_category", sa.String(length=32), nullable=False),
        sa.Column("supporting_citations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("contradicting_citations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("deterministic_checks", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.id"], name="fk_field_validation_results_run", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_field_validation_results"),
        sa.UniqueConstraint("validation_run_id", "field_name", name="uq_field_validation_results_run_field"),
    )
    op.create_index("ix_field_validation_results_validation_run_id", "field_validation_results", ["validation_run_id"])


def downgrade() -> None:
    op.drop_index("ix_field_validation_results_validation_run_id", table_name="field_validation_results")
    op.drop_table("field_validation_results")
    op.drop_index("ix_validation_runs_extraction_run_id", table_name="validation_runs")
    op.drop_index("ix_validation_runs_bid_id", table_name="validation_runs")
    op.drop_table("validation_runs")
