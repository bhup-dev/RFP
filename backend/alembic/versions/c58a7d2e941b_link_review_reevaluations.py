"""link review re-evaluations to their validation runs

Revision ID: c58a7d2e941b
Revises: b42f8c1d739e
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c58a7d2e941b"
down_revision: str | None = "b42f8c1d739e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "human_review_decisions",
        sa.Column("result_reference", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("human_review_decisions", "result_reference")
