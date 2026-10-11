"""add specialized agent findings

Revision ID: e91c5d7a34b2
Revises: d7b4a26f910e
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e91c5d7a34b2"
down_revision: Union[str, Sequence[str], None] = "d7b4a26f910e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "specialized_agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("evidence_chunk_count", sa.Integer(), server_default="0", nullable=False),
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
            "review_issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
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
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["bid_workflow_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_runs_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            ondelete="CASCADE",
            name="fk_agent_runs_bid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_specialized_agent_runs"),
        sa.UniqueConstraint(
            "workflow_id",
            "agent_name",
            name="uq_specialized_agent_runs_workflow_agent",
        ),
    )
    op.create_index(
        "ix_specialized_agent_runs_workflow_id",
        "specialized_agent_runs",
        ["workflow_id"],
    )
    op.create_index(
        "ix_specialized_agent_runs_bid_id", "specialized_agent_runs", ["bid_id"]
    )
    op.create_table(
        "specialized_agent_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(8, 5), nullable=True),
        sa.Column(
            "unresolved_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["specialized_agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_findings_run",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            ondelete="CASCADE",
            name="fk_agent_findings_bid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_specialized_agent_findings"),
        sa.UniqueConstraint(
            "agent_run_id",
            "finding_key",
            name="uq_specialized_agent_findings_run_key",
        ),
    )
    op.create_index(
        "ix_specialized_agent_findings_agent_run_id",
        "specialized_agent_findings",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_specialized_agent_findings_bid_id",
        "specialized_agent_findings",
        ["bid_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_specialized_agent_findings_bid_id",
        table_name="specialized_agent_findings",
    )
    op.drop_index(
        "ix_specialized_agent_findings_agent_run_id",
        table_name="specialized_agent_findings",
    )
    op.drop_table("specialized_agent_findings")
    op.drop_index(
        "ix_specialized_agent_runs_bid_id", table_name="specialized_agent_runs"
    )
    op.drop_index(
        "ix_specialized_agent_runs_workflow_id", table_name="specialized_agent_runs"
    )
    op.drop_table("specialized_agent_runs")
