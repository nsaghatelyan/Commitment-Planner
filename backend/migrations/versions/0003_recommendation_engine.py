"""recommendation engine output: recommendation columns, run summary, tenant risk profile

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("risk_profile", sa.String(16), nullable=False, server_default="balanced"),
    )
    op.add_column("analysis_run", sa.Column("risk_profile", sa.String(16)))
    op.add_column(
        "analysis_run",
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("analysis_run", "summary", server_default=None)

    op.alter_column("recommendation", "commitment_type", new_column_name="kind")
    op.alter_column("recommendation", "kind", type_=sa.String(32))
    op.alter_column(
        "recommendation", "estimated_monthly_savings", new_column_name="monthly_savings"
    )
    op.alter_column("recommendation", "estimated_savings_pct", new_column_name="savings_pct")
    op.alter_column("recommendation", "breakeven_months", new_column_name="breakeven_month")
    op.alter_column(
        "recommendation",
        "quantity",
        type_=sa.Numeric(precision=18, scale=4),
        postgresql_using="quantity::numeric",
    )
    op.add_column(
        "recommendation",
        sa.Column("source", sa.String(16), nullable=False, server_default="engine"),
    )
    op.alter_column("recommendation", "source", server_default=None)
    op.add_column(
        "recommendation",
        sa.Column("action", sa.String(16), nullable=False, server_default="purchase"),
    )
    op.alter_column("recommendation", "action", server_default=None)
    op.add_column("recommendation", sa.Column("plan_rank", sa.Integer()))
    op.add_column("recommendation", sa.Column("scope", sa.String(64)))
    op.add_column("recommendation", sa.Column("service", sa.String(128)))
    op.add_column("recommendation", sa.Column("instance_type", sa.String(64)))
    op.add_column("recommendation", sa.Column("monthly_cost_after", MONEY))
    op.add_column("recommendation", sa.Column("risk", sa.String(8)))
    op.add_column(
        "recommendation",
        sa.Column("urgent", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("recommendation", sa.Column("rationale", sa.Text()))
    op.create_index(
        op.f("ix_recommendation_analysis_run_id"), "recommendation", ["analysis_run_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recommendation_analysis_run_id"), table_name="recommendation")
    for column in (
        "rationale",
        "urgent",
        "risk",
        "monthly_cost_after",
        "instance_type",
        "service",
        "scope",
        "plan_rank",
        "action",
        "source",
    ):
        op.drop_column("recommendation", column)
    op.alter_column(
        "recommendation", "quantity", type_=sa.Integer(), postgresql_using="quantity::integer"
    )
    op.alter_column("recommendation", "breakeven_month", new_column_name="breakeven_months")
    op.alter_column("recommendation", "savings_pct", new_column_name="estimated_savings_pct")
    op.alter_column(
        "recommendation", "monthly_savings", new_column_name="estimated_monthly_savings"
    )
    op.alter_column("recommendation", "kind", type_=sa.String(64))
    op.alter_column("recommendation", "kind", new_column_name="commitment_type")
    op.drop_column("analysis_run", "summary")
    op.drop_column("analysis_run", "risk_profile")
    op.drop_column("tenant", "risk_profile")
