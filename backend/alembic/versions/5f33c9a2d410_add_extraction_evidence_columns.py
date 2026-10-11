"""add extraction evidence columns

Revision ID: 5f33c9a2d410
Revises: 821dff50e53f
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f33c9a2d410"
down_revision: Union[str, Sequence[str], None] = "821dff50e53f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist the evidence document set and authoritative field citations."""
    op.add_column(
        "extraction_runs",
        sa.Column(
            "document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "extracted_fields",
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove extraction evidence metadata."""
    op.drop_column("extracted_fields", "citations")
    op.drop_column("extraction_runs", "document_ids")
