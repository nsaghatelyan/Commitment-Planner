"""Approximate public prices and commitment discounts used by the synthetic generator.

The generator writes these into the price table too, so the engine reads the same numbers
from the database that produced the usage (as it would with real prices).
"""

from dataclasses import dataclass

from app.collectors.aws.usage_types import aws_size_factor

__all__ = ["aws_size_factor"]

# instance type -> (vCPU, on-demand $/hour Linux)
AWS_EC2 = {
    "t3.medium": (2, 0.0416),
    "t3.large": (2, 0.0832),
    "m5.large": (2, 0.096),
    "m5.xlarge": (4, 0.192),
    "m6i.large": (2, 0.096),
    "m6i.xlarge": (4, 0.192),
    "m6i.2xlarge": (8, 0.384),
    "m7i.xlarge": (4, 0.2016),
    "m7g.large": (2, 0.0816),
    "c5.xlarge": (4, 0.17),
    "c5.2xlarge": (8, 0.34),
    "c6i.xlarge": (4, 0.17),
    "c7i.2xlarge": (8, 0.357),
    "r5.xlarge": (4, 0.252),
}
AWS_WINDOWS_PER_VCPU = 0.046

# (instance class, engine, deployment) -> $/hour
AWS_RDS = {
    ("db.m5.large", "MySQL", "Single-AZ"): 0.171,
    ("db.r6g.xlarge", "PostgreSQL", "Multi-AZ"): 0.928,
    ("db.r5.xlarge", "PostgreSQL", "Multi-AZ"): 1.00,
    ("db.r5.xlarge", "SQL Server SE", "Multi-AZ"): 3.98,
}
# Engines whose RIs are not size-flexible (license included): exact type only.
AWS_RDS_EXACT_ENGINES = {"SQL Server SE", "SQL Server EE", "SQL Server Web", "Oracle SE2 (LI)"}

AWS_CACHE = {"cache.r6g.large": 0.206}
FARGATE_VCPU_HOUR = 0.04048
# Services billed per unit rather than per instance-hour ($ per unit).
DYNAMODB_RCU_HOUR = 0.00013
DYNAMODB_WCU_HOUR = 0.00065
LAMBDA_GB_SECOND = 0.0000166667
AWS_DOCDB = {"db.r6g.large": 0.277}
AWS_SAGEMAKER = {"ml.m5.xlarge": 0.23}

AZURE_VM = {
    "Standard_B2s": (2, 0.0416),
    "Standard_D2s_v3": (2, 0.096),
    "Standard_D4s_v3": (4, 0.192),
    "Standard_D8s_v3": (8, 0.384),
    "Standard_D4s_v4": (4, 0.192),
    "Standard_D4as_v5": (4, 0.172),
    "Standard_D4s_v5": (4, 0.192),
    "Standard_E4s_v5": (4, 0.252),
    "Standard_E8s_v3": (8, 0.504),
    "Standard_F4s_v2": (4, 0.169),
}
AZURE_WINDOWS_PER_VCPU = 0.046


@dataclass(frozen=True)
class AzureService:
    service: str
    sku: str
    units: float  # normalized units per instance (vCores, 100 RU/s, instances)
    od: float  # $/hour per instance
    usage_unit: str
    ri_kind: str  # discount table key
    sp_eligible: bool = False


AZURE_OTHER = {
    "cosmos": AzureService("Azure Cosmos DB", "Provisioned Throughput", 1, 0.008, "100 RU/s",
                           "ri_cosmos"),
    "sql_gp": AzureService("SQL Database", "SQLDB_GP_Gen5", 1, 0.2516, "vCore Hours", "ri_sql"),
    "sql_bc": AzureService("SQL Database", "SQLDB_BC_Gen5", 1, 0.6782, "vCore Hours", "ri_sql"),
    "app_p1v3": AzureService("Azure App Service", "P1v3", 1, 0.225, "1 Hour", "ri_appservice",
                             sp_eligible=True),
    "pg_d4ds": AzureService("Azure Database for PostgreSQL", "GP_Standard_D4ds_v5", 4, 0.356,
                            "1 Hour", "ri_postgres"),
}  # fmt: skip

# Discount off on-demand at No Upfront, by (provider, commitment, term months).
DISCOUNTS = {
    ("aws", "compute_sp", 12): 0.27,
    ("aws", "compute_sp", 36): 0.48,
    ("aws", "fargate_sp", 12): 0.20,
    ("aws", "fargate_sp", 36): 0.45,
    ("aws", "ec2_sp", 12): 0.33,
    ("aws", "ec2_sp", 36): 0.55,
    ("aws", "ri", 12): 0.38,
    # Database Savings Plans: 1-year No Upfront only, ~20% off on-demand.
    ("aws", "db_sp", 12): 0.20,
    # DynamoDB reserved capacity: 1 year, upfront fee + hourly (~54% off).
    ("aws", "dynamodb_ri", 12): 0.54,
    ("aws", "lambda_sp", 12): 0.12,
    ("aws", "lambda_sp", 36): 0.17,
    ("aws", "sagemaker_sp", 12): 0.27,
    ("aws", "sagemaker_sp", 36): 0.46,
    ("aws", "ri", 36): 0.58,
    ("azure", "sp", 12): 0.28,
    ("azure", "sp", 36): 0.48,
    ("azure", "ri", 12): 0.40,
    ("azure", "ri", 36): 0.60,
    ("azure", "ri_sql", 12): 0.33,
    ("azure", "ri_sql", 36): 0.55,
    ("azure", "ri_cosmos", 12): 0.20,
    ("azure", "ri_cosmos", 36): 0.30,
    ("azure", "ri_appservice", 12): 0.35,
    ("azure", "ri_appservice", 36): 0.55,
    ("azure", "ri_postgres", 12): 0.40,
    ("azure", "ri_postgres", 36): 0.60,
}
# AWS pays a little more discount for paying earlier; Azure prices don't depend on payment.
PAYMENT_UPLIFT = {"no_upfront": 0.0, "partial_upfront": 0.02, "all_upfront": 0.04}
UPFRONT_SHARE = {"no_upfront": 0.0, "partial_upfront": 0.5, "all_upfront": 1.0, "monthly": 0.0}
SPOT_DISCOUNT = 0.68


def discount(provider: str, kind: str, term: int, payment: str = "no_upfront") -> float:
    base = DISCOUNTS[(provider, kind, term)]
    return base + (PAYMENT_UPLIFT.get(payment, 0.0) if provider == "aws" else 0.0)
