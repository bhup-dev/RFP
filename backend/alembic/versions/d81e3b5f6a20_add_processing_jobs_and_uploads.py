"""add processing jobs and uploaded document metadata

Revision ID: d81e3b5f6a20
Revises: c58a7d2e941b
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d81e3b5f6a20"
down_revision: Union[str, Sequence[str], None] = "c58a7d2e941b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("document_role", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("original_filename", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_documents_bid_id_file_hash", "documents", ["bid_id", "file_hash"])

    op.create_table(
        "processing_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "job_type IN ('ingest_and_analyze', 'analyze_only')",
            name="ck_processing_jobs_job_type",
        ),
        sa.CheckConstraint(
            "stage IN ('queued', 'ingesting', 'analyzing', 'done')",
            name="ck_processing_jobs_stage",
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"], ["bids.id"], ondelete="CASCADE", name="fk_processing_jobs_bid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processing_jobs"),
    )
    op.create_index("ix_processing_jobs_bid_id", "processing_jobs", ["bid_id"])
    op.create_index(
        "ix_processing_jobs_status_created_at",
        "processing_jobs",
        ["status", "created_at"],
    )
    # At most one queued or running job per bid, enforced by the database.
    op.create_index(
        "uq_processing_jobs_active_bid",
        "processing_jobs",
        ["bid_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_processing_jobs_active_bid", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_status_created_at", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_bid_id", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_index("ix_documents_bid_id_file_hash", table_name="documents")
    op.drop_column("documents", "uploaded_at")
    op.drop_column("documents", "original_filename")
    op.drop_column("documents", "document_role")
