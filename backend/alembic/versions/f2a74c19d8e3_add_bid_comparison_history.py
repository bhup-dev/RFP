"""add bid comparison history

Revision ID: f2a74c19d8e3
Revises: e91c5d7a34b2
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a74c19d8e3"
down_revision: Union[str, Sequence[str], None] = "e91c5d7a34b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_comparison_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=32), server_default="processing", nullable=False
        ),
        sa.Column(
            "dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "allow_incomplete_profiles",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "request_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result",
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
        sa.Column("error_code", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_bid_comparison_runs"),
    )
    op.create_table(
        "bid_comparison_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("comparison_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("addendum_analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["comparison_id"],
            ["bid_comparison_runs.id"],
            ondelete="CASCADE",
            name="fk_comparison_profiles_comparison",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            ondelete="CASCADE",
            name="fk_comparison_profiles_bid",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            ondelete="SET NULL",
            name="fk_comparison_profiles_extraction",
        ),
        sa.ForeignKeyConstraint(
            ["addendum_analysis_run_id"],
            ["addendum_analysis_runs.id"],
            ondelete="SET NULL",
            name="fk_comparison_profiles_addendum",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.id"],
            ondelete="SET NULL",
            name="fk_comparison_profiles_validation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_comparison_profiles"),
        sa.UniqueConstraint(
            "comparison_id",
            "bid_id",
            name="uq_bid_comparison_profiles_comparison_bid",
        ),
    )
    op.create_index(
        "ix_bid_comparison_profiles_comparison_id",
        "bid_comparison_profiles",
        ["comparison_id"],
    )
    op.create_index(
        "ix_bid_comparison_profiles_bid_id",
        "bid_comparison_profiles",
        ["bid_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_comparison_profiles_bid_id", table_name="bid_comparison_profiles"
    )
    op.drop_index(
        "ix_bid_comparison_profiles_comparison_id",
        table_name="bid_comparison_profiles",
    )
    op.drop_table("bid_comparison_profiles")
    op.drop_table("bid_comparison_runs")
