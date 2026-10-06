"""Commitment pools: which usage a reservation or savings plan could cover, and how it is
measured (normalized units for size-flexible reservations, instance/unit counts for exact ones,
on-demand dollars for savings plans)."""

from dataclasses import dataclass

from app.collectors import types as k
from app.engine.data import UsageGroup
from app.pricing.keys import AWS_SERVICES

# LAYER_COMPUTE_SP holds the account-wide, region-flexible plans: Compute Savings Plans and
# Database Savings Plans (they cover disjoint services, so they share the last layer).
LAYER_RI, LAYER_INSTANCE_SP, LAYER_COMPUTE_SP = 1, 2, 3
# Usage Database Savings Plans cover that the engine prices (see app.pricing.aws).
AWS_DATABASE_SP_SERVICES = ("rds", "opensearch")

# RDS engines whose reservations are not size-flexible (license included).
RDS_EXACT_ENGINES = ("SQL Server", "Oracle SE1 (LI)", "Oracle SE2 (LI)", "Oracle SE (LI)")
AZURE_SP_SERVICES = {
    "Virtual Machines",
    "Azure App Service",
    "Functions",
    "Azure Container Apps",
    "Azure Dedicated Host",
}
# Azure services with reservations: measured in usage quantity (vCores, 100 RU/s, instances).
AZURE_RI_QTY_SERVICES = {
    "SQL Database": "vCores",
    "SQL Managed Instance": "vCores",
    "Azure Cosmos DB": "100 RU/s",
    "Azure App Service": "instances",
    "Azure Database for PostgreSQL": "instances",
    "Azure Database for MySQL": "instances",
    "Azure Cache for Redis": "instances",
}


@dataclass(frozen=True)
class Pool:
    layer: int
    provider: str
    kind: str  # commitment kind it would produce
    price_model: str  # ri | sp_instance | sp
    service: str | None = None
    region: str | None = None
    family: str | None = None
    instance_type: str | None = None  # exact-type reservations
    detail: str | None = None  # OS / engine / deployment the reservation is tied to
    measure: str = "units"  # units | qty | od

    @property
    def flexible(self) -> bool:
        return self.measure == "units"

    @property
    def unit_label(self) -> str:
        if self.measure == "od":
            return "$/hour on-demand"
        if self.measure == "units":
            return "normalized units"
        return AZURE_RI_QTY_SERVICES.get(self.service or "", "instances")

    @property
    def label(self) -> str:
        parts = [
            self.kind,
            self.service,
            self.region,
            self.instance_type or self.family,
            self.detail,
        ]
        return " / ".join(p for p in parts if p)

    def series(self, group: UsageGroup):
        if self.measure == "units":
            return group.units
        if self.measure == "qty":
            return group.qty
        return group.od


def ri_pool(g: UsageGroup) -> Pool | None:
    """The reservation pool a usage group belongs to, if it can be reserved at all."""
    a = g.attrs
    region, itype = a["region"], a["instance_type"]
    if not region:
        return None
    if a["provider"] == "aws":
        service = AWS_SERVICES.get(a["service_name"] or "")
        if not itype or service in (None, "fargate", "lambda"):
            return None
        if service == "ec2":
            # Linux, shared tenancy: regional and size-flexible. Other platforms: SP only.
            if (a["operating_system"] or "Linux") != "Linux" or (
                a["tenancy"] or "Shared"
            ) != "Shared":
                return None
            return Pool(
                LAYER_RI, "aws", k.AWS_RI, "ri", "ec2", region, a["instance_family"], detail="Linux"
            )
        if service == "rds":
            engine = a["database_engine"] or ""
            if engine.startswith(RDS_EXACT_ENGINES):
                return Pool(
                    LAYER_RI,
                    "aws",
                    k.AWS_RI,
                    "ri",
                    "rds",
                    region,
                    a["instance_family"],
                    itype,
                    f"{engine}, {a['deployment_option'] or 'Single-AZ'}",
                    "qty",
                )
            return Pool(
                LAYER_RI, "aws", k.AWS_RI, "ri", "rds", region, a["instance_family"], detail=engine
            )
        if service == "elasticache":
            return Pool(
                LAYER_RI, "aws", k.AWS_RI, "ri", "elasticache", region, a["instance_family"]
            )
        return Pool(
            LAYER_RI,
            "aws",
            k.AWS_RI,
            "ri",
            service,
            region,
            a["instance_family"],
            itype,
            measure="qty",
        )
    service = a["service_name"]
    if service == "Virtual Machines":
        if (a["operating_system"] or "Linux") != "Linux" or not a["instance_family"]:
            return None
        return Pool(LAYER_RI, "azure", k.AZURE_RI, "ri", service, region, a["instance_family"])
    if service in AZURE_RI_QTY_SERVICES and (itype or a["usage_unit"]):
        return Pool(
            LAYER_RI,
            "azure",
            k.AZURE_RI,
            "ri",
            service,
            region,
            None,
            itype or a["usage_unit"],
            measure="qty",
        )
    return None


def sp_pools(g: UsageGroup) -> list[Pool]:
    """Savings plan pools a usage group can be covered by, most specific first."""
    a = g.attrs
    if a["provider"] == "aws":
        service = AWS_SERVICES.get(a["service_name"] or "")
        out = []
        if service == "ec2" and a["instance_family"] and a["region"]:
            out.append(
                Pool(
                    LAYER_INSTANCE_SP,
                    "aws",
                    k.AWS_SP_EC2,
                    "sp_instance",
                    "ec2",
                    a["region"],
                    a["instance_family"],
                    measure="od",
                )
            )
        if service in ("ec2", "fargate", "lambda"):
            out.append(
                Pool(LAYER_COMPUTE_SP, "aws", k.AWS_SP_COMPUTE, "sp", "compute", measure="od")
            )
        if service in AWS_DATABASE_SP_SERVICES:
            out.append(
                Pool(
                    LAYER_COMPUTE_SP,
                    "aws",
                    k.AWS_SP_DATABASE,
                    "sp_database",
                    "database",
                    measure="od",
                )
            )
        return out
    if a["service_name"] in AZURE_SP_SERVICES:
        return [Pool(LAYER_COMPUTE_SP, "azure", k.AZURE_SP_COMPUTE, "sp", "compute", measure="od")]
    return []
