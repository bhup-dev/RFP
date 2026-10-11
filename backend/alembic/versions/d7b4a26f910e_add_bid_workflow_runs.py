"""add bid workflow runs

Revision ID: d7b4a26f910e
Revises: c4a91f8d2e60
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7b4a26f910e"
down_revision: Union[str, Sequence[str], None] = "c4a91f8d2e60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_workflow_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="running", nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column(
            "stage_statuses",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("eligible_document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("addendum_analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "summaries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_warnings', "
            "'needs_review', 'failed')",
            name="ck_bid_workflow_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bids.id"], ondelete="CASCADE", name="fk_workflow_runs_bid"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            ondelete="SET NULL",
            name="fk_workflow_runs_extraction",
        ),
        sa.ForeignKeyConstraint(
            ["addendum_analysis_run_id"],
            ["addendum_analysis_runs.id"],
            ondelete="SET NULL",
            name="fk_workflow_runs_addendum",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.id"],
            ondelete="SET NULL",
            name="fk_workflow_runs_validation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_workflow_runs"),
    )
    op.create_index("ix_bid_workflow_runs_bid_id", "bid_workflow_runs", ["bid_id"])
    op.create_index(
        "ix_bid_workflow_runs_extraction_run_id",
        "bid_workflow_runs",
        ["extraction_run_id"],
    )
    op.create_index(
        "ix_bid_workflow_runs_addendum_analysis_run_id",
        "bid_workflow_runs",
        ["addendum_analysis_run_id"],
    )
    op.create_index(
        "ix_bid_workflow_runs_validation_run_id",
        "bid_workflow_runs",
        ["validation_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_workflow_runs_validation_run_id", table_name="bid_workflow_runs"
    )
    op.drop_index(
        "ix_bid_workflow_runs_addendum_analysis_run_id",
        table_name="bid_workflow_runs",
    )
    op.drop_index(
        "ix_bid_workflow_runs_extraction_run_id", table_name="bid_workflow_runs"
    )
    op.drop_index("ix_bid_workflow_runs_bid_id", table_name="bid_workflow_runs")
    op.drop_table("bid_workflow_runs")
