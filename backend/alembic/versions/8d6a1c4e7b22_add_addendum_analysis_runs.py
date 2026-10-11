"""add addendum analysis runs

Revision ID: 8d6a1c4e7b22
Revises: 5f33c9a2d410
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8d6a1c4e7b22"
down_revision: Union[str, Sequence[str], None] = "5f33c9a2d410"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create append-only analysis runs and link requirement-level findings."""
    op.create_table(
        "addendum_analysis_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column(
            "original_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "addendum_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "relationship_uncertain",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "ordering_uncertain",
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
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            name="fk_addendum_analysis_runs_bid_id_bids",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_addendum_analysis_runs"),
    )
    op.create_index(
        "ix_addendum_analysis_runs_bid_id",
        "addendum_analysis_runs",
        ["bid_id"],
        unique=False,
    )

    op.add_column(
        "addendum_changes",
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "addendum_changes",
        sa.Column("original_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "addendum_changes",
        sa.Column("addendum_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("addendum_changes", sa.Column("impact_note", sa.Text(), nullable=True))
    op.add_column(
        "addendum_changes", sa.Column("severity", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "addendum_changes",
        sa.Column("confidence", sa.Numeric(precision=8, scale=5), nullable=True),
    )
    op.create_foreign_key(
        "fk_addendum_changes_analysis_run_id_addendum_analysis_runs",
        "addendum_changes",
        "addendum_analysis_runs",
        ["analysis_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_addendum_changes_original_document_id_documents",
        "addendum_changes",
        "documents",
        ["original_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_addendum_changes_addendum_document_id_documents",
        "addendum_changes",
        "documents",
        ["addendum_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_addendum_changes_analysis_run_id",
        "addendum_changes",
        ["analysis_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove addendum analysis lifecycle metadata."""
    op.drop_index("ix_addendum_changes_analysis_run_id", table_name="addendum_changes")
    op.drop_constraint(
        "fk_addendum_changes_addendum_document_id_documents",
        "addendum_changes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_addendum_changes_original_document_id_documents",
        "addendum_changes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_addendum_changes_analysis_run_id_addendum_analysis_runs",
        "addendum_changes",
        type_="foreignkey",
    )
    op.drop_column("addendum_changes", "confidence")
    op.drop_column("addendum_changes", "severity")
    op.drop_column("addendum_changes", "impact_note")
    op.drop_column("addendum_changes", "addendum_document_id")
    op.drop_column("addendum_changes", "original_document_id")
    op.drop_column("addendum_changes", "analysis_run_id")
    op.drop_index(
        "ix_addendum_analysis_runs_bid_id", table_name="addendum_analysis_runs"
    )
    op.drop_table("addendum_analysis_runs")
