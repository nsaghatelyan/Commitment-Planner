import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin

# Plain strings rather than Postgres enums keep migrations cheap while the value sets settle.
# provider: "aws" | "azure"
# run status: "pending" | "running" | "succeeded" | "failed"

MONEY = Numeric(18, 6)


def tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Tenant(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # conservative | balanced | aggressive: how far commitments are sized above the floor.
    risk_profile: Mapped[str] = mapped_column(
        String(16), nullable=False, default="balanced", server_default="balanced"
    )


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "user"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")


class CloudConnection(IdMixin, TimestampMixin, Base):
    """How we reach a client's cloud: an AWS role to assume, or an Azure billing scope that our
    multi-tenant Entra app has been granted. No client secrets are stored."""

    __tablename__ = "cloud_connection"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # AWS. auth mode "role": assume aws_role_arn with aws_external_id (the client's account).
    # "profile": use an AWS CLI profile on the machine running the tool (local testing).
    aws_auth_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="role", server_default="role"
    )
    aws_profile: Mapped[str | None] = mapped_column(String(128))
    aws_role_arn: Mapped[str | None] = mapped_column(String(2048))
    aws_external_id: Mapped[str | None] = mapped_column(String(256), unique=True)
    aws_export_bucket: Mapped[str | None] = mapped_column(String(255))
    aws_export_prefix: Mapped[str | None] = mapped_column(String(1024))
    # Azure
    azure_tenant_id: Mapped[str | None] = mapped_column(String(64))
    # App registration to sign in with (defaults to the tool's multi-tenant app) and where its
    # credential lives: a certificate path, or "env:VAR" naming a client-secret env variable.
    azure_client_id: Mapped[str | None] = mapped_column(String(64))
    azure_credential_ref: Mapped[str | None] = mapped_column(String(1024))
    # ea | mca | payg | csp
    azure_agreement_type: Mapped[str | None] = mapped_column(String(8))
    azure_billing_scope: Mapped[str | None] = mapped_column(String(1024))
    # https://<account>.blob.core.windows.net/<container>[/<prefix>]
    azure_export_container: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class CloudAccount(IdMixin, TimestampMixin, Base):
    """An AWS account or Azure subscription discovered through a connection."""

    __tablename__ = "cloud_account"
    __table_args__ = (UniqueConstraint("cloud_connection_id", "external_account_id"),)

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    cloud_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_connection.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    is_payer: Mapped[bool] = mapped_column(nullable=False, default=False)


class Commitment(IdMixin, TimestampMixin, Base):
    """An existing savings plan or reservation the client already owns."""

    __tablename__ = "commitment"
    __table_args__ = (UniqueConstraint("cloud_connection_id", "provider_commitment_id"),)

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    cloud_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_connection.id", ondelete="CASCADE"), nullable=False
    )
    # Owning account/subscription when known; org- or billing-scope commitments may have none.
    cloud_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_account.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # aws_sp_compute | aws_sp_ec2 | aws_sp_sagemaker | aws_sp_database | aws_ri
    # | azure_sp_compute | azure_ri
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_commitment_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # AWS: organization | Region | Availability Zone; Azure: Shared | Single | ManagementGroup
    scope: Mapped[str | None] = mapped_column(String(64))
    service: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(64))
    instance_family: Mapped[str | None] = mapped_column(String(64))
    instance_type: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[int | None]
    # Savings plans: the hourly commitment.
    hourly_commitment: Mapped[Decimal | None] = mapped_column(MONEY)
    term_months: Mapped[int] = mapped_column(nullable=False)
    payment_option: Mapped[str | None] = mapped_column(String(32))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Totals for the whole commitment (all units).
    upfront_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    recurring_hourly_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    # Upfront spread over the term plus recurring: Used + Unused effective cost per hour.
    amortized_hourly_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    state: Mapped[str | None] = mapped_column(String(32))
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CommitmentUtilization(IdMixin, Base):
    __tablename__ = "commitment_utilization"
    __table_args__ = (UniqueConstraint("commitment_id", "date"),)

    commitment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commitment.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    utilization_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    unused_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    used_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    net_savings: Mapped[Decimal | None] = mapped_column(MONEY)


class Price(IdMixin, Base):
    """Public on-demand and commitment rates, shared across tenants."""

    __tablename__ = "price"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "sku_key",
            "region",
            "pricing_model",
            "term_months",
            "payment_option",
            "effective_from",
            name="uq_price_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    # See app.pricing.keys for the per-provider conventions.
    sku_key: Mapped[str] = mapped_column(String(256), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    # on_demand | sp (Compute SP, Azure SP) | sp_instance (AWS EC2 Instance SP) | ri
    pricing_model: Mapped[str] = mapped_column(String(16), nullable=False)
    term_months: Mapped[int | None]
    payment_option: Mapped[str | None] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    # Per unit (per hour for compute). For ri/sp, upfront is amortized into the rate.
    # 14 decimals: per-request and per-unit prices go down to $0.0000000016 (ElastiCache ECPU).
    price_per_unit: Mapped[Decimal] = mapped_column(Numeric(24, 14), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CollectionRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "collection_run"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    cloud_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_connection.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # Where this run's Parquet files live (local dir or s3:// prefix).
    usage_location: Mapped[str | None] = mapped_column(String(2048))
    rows_ingested: Mapped[int | None]
    # Provider API calls actually sent (cache hits excluded); Cost Explorer bills $0.01 each.
    api_calls: Mapped[int | None]
    # Per-API call counts, cache hits, sources used, warnings.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AnalysisRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "analysis_run"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    lookback_days: Mapped[int] = mapped_column(nullable=False, default=30)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    engine_version: Mapped[str | None] = mapped_column(String(32))
    risk_profile: Mapped[str | None] = mapped_column(String(16))
    # Per-client summary: spend, coverage, utilization, expiring/stranded commitments,
    # ordered purchase plan, projected savings, comparison with native recommendations.
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Recommendation(IdMixin, TimestampMixin, Base):
    __tablename__ = "recommendation"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cloud_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_account.id", ondelete="SET NULL")
    )
    # native: the provider's own recommendation; engine: our simulation.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # purchase | renew | exchange | flag (an existing commitment that needs attention)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="purchase")
    # Order in the purchase plan (layering order); null for native rows.
    plan_rank: Mapped[int | None]
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # Same values as commitment.kind, e.g. aws_sp_compute, aws_ri, azure_ri.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(64))
    service: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(64))
    instance_family: Mapped[str | None] = mapped_column(String(64))
    instance_type: Mapped[str | None] = mapped_column(String(64))
    term_months: Mapped[int] = mapped_column(nullable=False)
    payment_option: Mapped[str | None] = mapped_column(String(32))
    hourly_commitment: Mapped[Decimal | None] = mapped_column(MONEY)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    upfront_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    monthly_cost_after: Mapped[Decimal | None] = mapped_column(MONEY)
    monthly_savings: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    savings_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    expected_utilization_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    breakeven_month: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # low | med | high
    risk: Mapped[str | None] = mapped_column(String(8))
    urgent: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    rationale: Mapped[str | None] = mapped_column(Text)
    # open | accepted | dismissed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    # Term/payment alternatives, stability stats, chart series, pool key, etc.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Report(IdMixin, TimestampMixin, Base):
    __tablename__ = "report"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="pdf")
    storage_location: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_log"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
