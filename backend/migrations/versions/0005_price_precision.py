"""price precision: 14 decimals for per-request and per-unit prices

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "price",
        "price_per_unit",
        type_=sa.Numeric(24, 14),
        existing_type=sa.Numeric(18, 8),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "price",
        "price_per_unit",
        type_=sa.Numeric(18, 8),
        existing_type=sa.Numeric(24, 14),
        existing_nullable=False,
    )
