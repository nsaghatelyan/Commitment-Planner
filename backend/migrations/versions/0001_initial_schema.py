"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-05 15:03:47.598732

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price",
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=256), nullable=False),
        sa.Column("pricing_model", sa.String(length=32), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("payment_option", sa.String(length=32), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price")),
    )
    op.create_index(
        "ix_price_lookup",
        "price",
        ["provider", "service", "region", "sku", "pricing_model", "term_months", "payment_option"],
        unique=False,
    )
    op.create_table(
        "tenant",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenant_slug")),
    )
    op.create_table(
        "analysis_run",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_analysis_run_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_run")),
    )
    op.create_index(op.f("ix_analysis_run_tenant_id"), "analysis_run", ["tenant_id"], unique=False)
    op.create_table(
        "cloud_connection",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role_arn", sa.String(length=2048), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("azure_tenant_id", sa.String(length=64), nullable=True),
        sa.Column("azure_client_id", sa.String(length=64), nullable=True),
        sa.Column("credential_secret_ref", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_cloud_connection_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cloud_connection")),
    )
    op.create_index(
        op.f("ix_cloud_connection_tenant_id"), "cloud_connection", ["tenant_id"], unique=False
    )
    op.create_table(
        "user",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_user_tenant_id_tenant"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("email", name=op.f("uq_user_email")),
    )
    op.create_index(op.f("ix_user_tenant_id"), "user", ["tenant_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_audit_log_tenant_id_tenant"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_audit_log_user_id_user"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_occurred_at"), "audit_log", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"], unique=False)
    op.create_table(
        "cloud_account",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("cloud_connection_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("is_payer", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["cloud_connection_id"],
            ["cloud_connection.id"],
            name=op.f("fk_cloud_account_cloud_connection_id_cloud_connection"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_cloud_account_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cloud_account")),
        sa.UniqueConstraint(
            "cloud_connection_id",
            "external_account_id",
            name=op.f("uq_cloud_account_cloud_connection_id"),
        ),
    )
    op.create_index(
        op.f("ix_cloud_account_tenant_id"), "cloud_account", ["tenant_id"], unique=False
    )
    op.create_table(
        "collection_run",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("cloud_connection_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("usage_location", sa.String(length=2048), nullable=True),
        sa.Column("rows_collected", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["cloud_connection_id"],
            ["cloud_connection.id"],
            name=op.f("fk_collection_run_cloud_connection_id_cloud_connection"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_collection_run_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_run")),
    )
    op.create_index(
        op.f("ix_collection_run_tenant_id"), "collection_run", ["tenant_id"], unique=False
    )
    op.create_table(
        "report",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("analysis_run_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("storage_location", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_run.id"],
            name=op.f("fk_report_analysis_run_id_analysis_run"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name=op.f("fk_report_created_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_report_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report")),
    )
    op.create_index(op.f("ix_report_tenant_id"), "report", ["tenant_id"], unique=False)
    op.create_table(
        "commitment",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("cloud_account_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("commitment_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("payment_option", sa.String(length=32), nullable=True),
        sa.Column("hourly_commitment", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("instance_family", sa.String(length=64), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["cloud_account_id"],
            ["cloud_account.id"],
            name=op.f("fk_commitment_cloud_account_id_cloud_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_commitment_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitment")),
        sa.UniqueConstraint(
            "cloud_account_id", "external_id", name=op.f("uq_commitment_cloud_account_id")
        ),
    )
    op.create_index(op.f("ix_commitment_tenant_id"), "commitment", ["tenant_id"], unique=False)
    op.create_table(
        "recommendation",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("analysis_run_id", sa.UUID(), nullable=False),
        sa.Column("cloud_account_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("commitment_type", sa.String(length=64), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("payment_option", sa.String(length=32), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("instance_family", sa.String(length=64), nullable=True),
        sa.Column("hourly_commitment", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("upfront_cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("estimated_monthly_savings", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("estimated_savings_pct", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("expected_utilization_pct", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("breakeven_months", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_run.id"],
            name=op.f("fk_recommendation_analysis_run_id_analysis_run"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cloud_account_id"],
            ["cloud_account.id"],
            name=op.f("fk_recommendation_cloud_account_id_cloud_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_recommendation_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation")),
    )
    op.create_index(
        op.f("ix_recommendation_tenant_id"), "recommendation", ["tenant_id"], unique=False
    )
    op.create_table(
        "commitment_utilization",
        sa.Column("commitment_id", sa.UUID(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("utilization_pct", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("used_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("unused_amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("net_savings", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitment.id"],
            name=op.f("fk_commitment_utilization_commitment_id_commitment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitment_utilization")),
        sa.UniqueConstraint(
            "commitment_id", "usage_date", name=op.f("uq_commitment_utilization_commitment_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("commitment_utilization")
    op.drop_index(op.f("ix_recommendation_tenant_id"), table_name="recommendation")
    op.drop_table("recommendation")
    op.drop_index(op.f("ix_commitment_tenant_id"), table_name="commitment")
    op.drop_table("commitment")
    op.drop_index(op.f("ix_report_tenant_id"), table_name="report")
    op.drop_table("report")
    op.drop_index(op.f("ix_collection_run_tenant_id"), table_name="collection_run")
    op.drop_table("collection_run")
    op.drop_index(op.f("ix_cloud_account_tenant_id"), table_name="cloud_account")
    op.drop_table("cloud_account")
    op.drop_index(op.f("ix_audit_log_tenant_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_occurred_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_user_tenant_id"), table_name="user")
    op.drop_table("user")
    op.drop_index(op.f("ix_cloud_connection_tenant_id"), table_name="cloud_connection")
    op.drop_table("cloud_connection")
    op.drop_index(op.f("ix_analysis_run_tenant_id"), table_name="analysis_run")
    op.drop_table("analysis_run")
    op.drop_table("tenant")
    op.drop_index("ix_price_lookup", table_name="price")
    op.drop_table("price")
