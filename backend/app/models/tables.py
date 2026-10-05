import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
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


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "user"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")


class CloudConnection(IdMixin, TimestampMixin, Base):
    """How we reach a client's cloud: an AWS role to assume, or an Azure service principal."""

    __tablename__ = "cloud_connection"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # AWS
    role_arn: Mapped[str | None] = mapped_column(String(2048))
    external_id: Mapped[str | None] = mapped_column(String(256))
    # Azure
    azure_tenant_id: Mapped[str | None] = mapped_column(String(64))
    azure_client_id: Mapped[str | None] = mapped_column(String(64))
    # Reference to the secret in the secrets store; the secret itself is never stored here.
    credential_secret_ref: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (UniqueConstraint("cloud_account_id", "external_id"),)

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    cloud_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_account.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # e.g. compute_savings_plan, ec2_instance_sp, reserved_instance, azure_reservation, azure_savings_plan
    commitment_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    term_months: Mapped[int] = mapped_column(nullable=False)
    payment_option: Mapped[str | None] = mapped_column(String(32))
    hourly_commitment: Mapped[Decimal | None] = mapped_column(MONEY)
    quantity: Mapped[int | None]
    region: Mapped[str | None] = mapped_column(String(64))
    instance_family: Mapped[str | None] = mapped_column(String(64))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CommitmentUtilization(IdMixin, Base):
    __tablename__ = "commitment_utilization"
    __table_args__ = (UniqueConstraint("commitment_id", "usage_date"),)

    commitment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commitment.id", ondelete="CASCADE"), nullable=False
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    utilization_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    used_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    unused_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    net_savings: Mapped[Decimal | None] = mapped_column(MONEY)


class Price(IdMixin, Base):
    """Public on-demand and commitment rates, shared across tenants."""

    __tablename__ = "price"
    __table_args__ = (
        Index(
            "ix_price_lookup",
            "provider",
            "service",
            "region",
            "sku",
            "pricing_model",
            "term_months",
            "payment_option",
        ),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    sku: Mapped[str] = mapped_column(String(256), nullable=False)
    # on_demand | savings_plan | reservation
    pricing_model: Mapped[str] = mapped_column(String(32), nullable=False)
    term_months: Mapped[int | None]
    payment_option: Mapped[str | None] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    price_per_unit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
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
    rows_collected: Mapped[int | None]
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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Recommendation(IdMixin, TimestampMixin, Base):
    __tablename__ = "recommendation"

    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="CASCADE"), nullable=False
    )
    cloud_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cloud_account.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    commitment_type: Mapped[str] = mapped_column(String(64), nullable=False)
    term_months: Mapped[int] = mapped_column(nullable=False)
    payment_option: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(64))
    instance_family: Mapped[str | None] = mapped_column(String(64))
    hourly_commitment: Mapped[Decimal | None] = mapped_column(MONEY)
    quantity: Mapped[int | None]
    upfront_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    estimated_monthly_savings: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    estimated_savings_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    expected_utilization_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    breakeven_months: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # open | accepted | dismissed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
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
