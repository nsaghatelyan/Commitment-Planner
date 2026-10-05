"""align connection, commitment, price and run columns with the design

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(precision=18, scale=6)


def upgrade() -> None:
    # cloud_connection
    op.alter_column("cloud_connection", "role_arn", new_column_name="aws_role_arn")
    op.alter_column("cloud_connection", "external_id", new_column_name="aws_external_id")
    op.drop_column("cloud_connection", "azure_client_id")
    op.drop_column("cloud_connection", "credential_secret_ref")
    op.add_column("cloud_connection", sa.Column("aws_export_bucket", sa.String(255)))
    op.add_column("cloud_connection", sa.Column("aws_export_prefix", sa.String(1024)))
    op.add_column("cloud_connection", sa.Column("azure_agreement_type", sa.String(8)))
    op.add_column("cloud_connection", sa.Column("azure_billing_scope", sa.String(1024)))
    op.add_column("cloud_connection", sa.Column("azure_export_container", sa.String(2048)))
    op.add_column("cloud_connection", sa.Column("last_success_at", sa.DateTime(timezone=True)))
    op.add_column("cloud_connection", sa.Column("last_error", sa.Text()))
    op.create_unique_constraint(
        op.f("uq_cloud_connection_aws_external_id"), "cloud_connection", ["aws_external_id"]
    )

    # commitment
    op.drop_constraint("uq_commitment_cloud_account_id", "commitment", type_="unique")
    op.alter_column("commitment", "commitment_type", new_column_name="kind")
    op.alter_column("commitment", "kind", type_=sa.String(32))
    op.alter_column("commitment", "external_id", new_column_name="provider_commitment_id")
    op.alter_column("commitment", "provider_commitment_id", type_=sa.String(512))
    op.add_column("commitment", sa.Column("cloud_connection_id", sa.UUID(), nullable=False))
    op.create_foreign_key(
        op.f("fk_commitment_cloud_connection_id_cloud_connection"),
        "commitment",
        "cloud_connection",
        ["cloud_connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("commitment", "cloud_account_id", nullable=True)
    op.drop_constraint(
        "fk_commitment_cloud_account_id_cloud_account", "commitment", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_commitment_cloud_account_id_cloud_account"),
        "commitment",
        "cloud_account",
        ["cloud_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("commitment", sa.Column("scope", sa.String(64)))
    op.add_column("commitment", sa.Column("service", sa.String(128)))
    op.add_column("commitment", sa.Column("instance_type", sa.String(64)))
    op.add_column("commitment", sa.Column("upfront_cost", MONEY))
    op.add_column("commitment", sa.Column("recurring_hourly_cost", MONEY))
    op.add_column("commitment", sa.Column("amortized_hourly_cost", MONEY))
    op.add_column("commitment", sa.Column("state", sa.String(32)))
    op.create_unique_constraint(
        op.f("uq_commitment_cloud_connection_id"),
        "commitment",
        ["cloud_connection_id", "provider_commitment_id"],
    )

    # commitment_utilization
    op.drop_constraint(
        "uq_commitment_utilization_commitment_id", "commitment_utilization", type_="unique"
    )
    op.alter_column("commitment_utilization", "usage_date", new_column_name="date")
    op.alter_column("commitment_utilization", "unused_amount", new_column_name="unused_cost")
    op.create_unique_constraint(
        op.f("uq_commitment_utilization_commitment_id"),
        "commitment_utilization",
        ["commitment_id", "date"],
    )

    # price
    op.drop_index("ix_price_lookup", table_name="price")
    op.alter_column("price", "sku", new_column_name="sku_key")
    op.alter_column("price", "effective_date", new_column_name="effective_from")
    op.alter_column("price", "pricing_model", type_=sa.String(16))
    op.alter_column("price", "price_per_unit", type_=sa.Numeric(precision=18, scale=8))
    op.create_unique_constraint(
        "uq_price_identity",
        "price",
        [
            "provider",
            "sku_key",
            "region",
            "pricing_model",
            "term_months",
            "payment_option",
            "effective_from",
        ],
        postgresql_nulls_not_distinct=True,
    )

    # collection_run
    op.alter_column("collection_run", "rows_collected", new_column_name="rows_ingested")
    op.add_column("collection_run", sa.Column("api_calls", sa.Integer()))
    op.add_column(
        "collection_run",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("collection_run", "details", server_default=None)


def downgrade() -> None:
    op.drop_column("collection_run", "details")
    op.drop_column("collection_run", "api_calls")
    op.alter_column("collection_run", "rows_ingested", new_column_name="rows_collected")

    op.drop_constraint("uq_price_identity", "price", type_="unique")
    op.alter_column("price", "price_per_unit", type_=MONEY)
    op.alter_column("price", "pricing_model", type_=sa.String(32))
    op.alter_column("price", "effective_from", new_column_name="effective_date")
    op.alter_column("price", "sku_key", new_column_name="sku")
    op.create_index(
        "ix_price_lookup",
        "price",
        ["provider", "service", "region", "sku", "pricing_model", "term_months", "payment_option"],
    )

    op.drop_constraint(
        op.f("uq_commitment_utilization_commitment_id"), "commitment_utilization", type_="unique"
    )
    op.alter_column("commitment_utilization", "unused_cost", new_column_name="unused_amount")
    op.alter_column("commitment_utilization", "date", new_column_name="usage_date")
    op.create_unique_constraint(
        "uq_commitment_utilization_commitment_id",
        "commitment_utilization",
        ["commitment_id", "usage_date"],
    )

    op.drop_constraint(op.f("uq_commitment_cloud_connection_id"), "commitment", type_="unique")
    for column in (
        "state",
        "amortized_hourly_cost",
        "recurring_hourly_cost",
        "upfront_cost",
        "instance_type",
        "service",
        "scope",
    ):
        op.drop_column("commitment", column)
    op.drop_constraint(
        op.f("fk_commitment_cloud_account_id_cloud_account"), "commitment", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_commitment_cloud_account_id_cloud_account",
        "commitment",
        "cloud_account",
        ["cloud_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("commitment", "cloud_account_id", nullable=False)
    op.drop_constraint(
        op.f("fk_commitment_cloud_connection_id_cloud_connection"),
        "commitment",
        type_="foreignkey",
    )
    op.drop_column("commitment", "cloud_connection_id")
    op.alter_column("commitment", "provider_commitment_id", type_=sa.String(256))
    op.alter_column("commitment", "provider_commitment_id", new_column_name="external_id")
    op.alter_column("commitment", "kind", type_=sa.String(64))
    op.alter_column("commitment", "kind", new_column_name="commitment_type")
    op.create_unique_constraint(
        "uq_commitment_cloud_account_id", "commitment", ["cloud_account_id", "external_id"]
    )

    op.drop_constraint(
        op.f("uq_cloud_connection_aws_external_id"), "cloud_connection", type_="unique"
    )
    for column in (
        "last_error",
        "last_success_at",
        "azure_export_container",
        "azure_billing_scope",
        "azure_agreement_type",
        "aws_export_prefix",
        "aws_export_bucket",
    ):
        op.drop_column("cloud_connection", column)
    op.add_column("cloud_connection", sa.Column("credential_secret_ref", sa.String(2048)))
    op.add_column("cloud_connection", sa.Column("azure_client_id", sa.String(64)))
    op.alter_column("cloud_connection", "aws_external_id", new_column_name="external_id")
    op.alter_column("cloud_connection", "aws_role_arn", new_column_name="role_arn")
