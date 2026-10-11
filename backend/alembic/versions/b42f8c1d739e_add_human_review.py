"""add durable human review items and decisions

Revision ID: b42f8c1d739e
Revises: a13e6f8c42d9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b42f8c1d739e"
down_revision: str | None = "a13e6f8c42d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_bid_workflow_runs_status", "bid_workflow_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_bid_workflow_runs_status",
        "bid_workflow_runs",
        "status IN ('running', 'pending_review', 'completed', "
        "'completed_with_warnings', 'needs_review', 'failed')",
    )
    op.create_table(
        "human_review_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_stage", sa.String(length=96), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("item_reference", sa.String(length=255), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=True),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=True),
        sa.Column("validation_status", sa.String(length=64), nullable=True),
        sa.Column("confidence_score", sa.Numeric(8, 5), nullable=True),
        sa.Column(
            "supporting_citations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "contradicting_citations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("reviewer_id", sa.String(length=255), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("corrected_value", postgresql.JSONB(), nullable=True),
        sa.Column("reevaluation_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', "
            "'reevaluation_requested', 'unresolved')",
            name="ck_human_review_items_status",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bids.id"], ondelete="CASCADE", name="fk_review_items_bid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_review_items"),
        sa.UniqueConstraint(
            "workflow_id",
            "item_reference",
            name="uq_human_review_items_workflow_reference",
        ),
    )
    op.create_index("ix_human_review_items_bid_id", "human_review_items", ["bid_id"])
    op.create_index(
        "ix_human_review_items_workflow_id", "human_review_items", ["workflow_id"]
    )
    op.create_table(
        "human_review_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "review_item_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.String(length=255), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("corrected_value", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["human_review_items.id"],
            ondelete="CASCADE",
            name="fk_review_decisions_item",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_review_decisions"),
        sa.UniqueConstraint(
            "review_item_id",
            "idempotency_key",
            name="uq_human_review_decisions_item_idempotency",
        ),
    )
    op.create_index(
        "ix_human_review_decisions_review_item_id",
        "human_review_decisions",
        ["review_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_human_review_decisions_review_item_id",
        table_name="human_review_decisions",
    )
    op.drop_table("human_review_decisions")
    op.drop_index("ix_human_review_items_workflow_id", table_name="human_review_items")
    op.drop_index("ix_human_review_items_bid_id", table_name="human_review_items")
    op.drop_table("human_review_items")
    op.execute(
        "UPDATE bid_workflow_runs SET status = 'needs_review' "
        "WHERE status = 'pending_review'"
    )
    op.drop_constraint(
        "ck_bid_workflow_runs_status", "bid_workflow_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_bid_workflow_runs_status",
        "bid_workflow_runs",
        "status IN ('running', 'completed', 'completed_with_warnings', "
        "'needs_review', 'failed')",
    )
