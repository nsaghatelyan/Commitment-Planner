"""sku_key conventions, so usage rows and price rows can be matched by the engine.

EC2 keys are built from instance type, OS and tenancy. Every other AWS service is keyed by the
usage type and operation AWS bills it under ("rds|InstanceUsage:db.m5.large|CreateDBInstance:0002"):
usage (Cost Explorer, CUR, FOCUS), the Pricing API and Savings Plans rates all carry both, so the
key is exact for any service and unit (instance-hours, capacity units, ACU/DCU/RPU, requests).
"""

from app.collectors.aws.usage_types import REGION_PREFIXES

_AWS_SQL = {"SQL Std": "SQL Server Standard", "SQL Ent": "SQL Server Enterprise",
            "SQL Web": "SQL Server Web"}  # fmt: skip
_AWS_OS = {
    "Linux/UNIX": "Linux",
    "Red Hat Enterprise Linux": "RHEL",
    "Red Hat Enterprise Linux with HA": "RHEL with HA",
    "SUSE Linux": "SUSE",
    "Windows BYOL": "Windows BYOL",
}
# Cost Explorer / FOCUS service names and CUR product codes -> short service used in keys and
# the price table.
AWS_SERVICES = {
    "Amazon Elastic Compute Cloud - Compute": "ec2",
    "Amazon Elastic Compute Cloud": "ec2",
    "AmazonEC2": "ec2",
    "Amazon Relational Database Service": "rds",
    "AmazonRDS": "rds",
    "Amazon ElastiCache": "elasticache",
    "AmazonElastiCache": "elasticache",
    "Amazon OpenSearch Service": "opensearch",
    "Amazon Elasticsearch Service": "opensearch",
    "AmazonES": "opensearch",
    "Amazon Redshift": "redshift",
    "AmazonRedshift": "redshift",
    "Amazon MemoryDB": "memorydb",
    "AmazonMemoryDB": "memorydb",
    "Amazon DynamoDB": "dynamodb",
    "AmazonDynamoDB": "dynamodb",
    "Amazon DocumentDB (with MongoDB compatibility)": "docdb",
    "AmazonDocDB": "docdb",
    "Amazon Neptune": "neptune",
    "AmazonNeptune": "neptune",
    "Amazon Timestream": "timestream",
    "AmazonTimestream": "timestream",
    "AWS Database Migration Service": "dms",
    "AWSDatabaseMigrationSvc": "dms",
    "Amazon Keyspaces (for Apache Cassandra)": "keyspaces",
    "AmazonMCS": "keyspaces",
    "Amazon Aurora DSQL": "dsql",
    "AuroraDSQL": "dsql",
    "Amazon Elastic Container Service": "fargate",
    "AmazonECS": "fargate",
    "AWS Lambda": "lambda",
    "AWSLambda": "lambda",
    "Amazon SageMaker": "sagemaker",
    "AmazonSageMaker": "sagemaker",
}
# Name variants seen across sources and over time ("Amazon SageMaker AI", "Amazon MemoryDB for
# Redis", "Amazon Timestream for InfluxDB", ...).
_AWS_SERVICE_PREFIXES = {
    "Amazon SageMaker": "sagemaker",
    "Amazon MemoryDB": "memorydb",
    "Amazon Timestream": "timestream",
    "Amazon DocumentDB": "docdb",
    "Amazon Keyspaces": "keyspaces",
    "Amazon Aurora DSQL": "dsql",
}
# Short service -> Pricing API service code.
AWS_SERVICE_CODES = {
    "ec2": "AmazonEC2",
    "rds": "AmazonRDS",
    "elasticache": "AmazonElastiCache",
    "opensearch": "AmazonES",
    "redshift": "AmazonRedshift",
    "memorydb": "AmazonMemoryDB",
    "dynamodb": "AmazonDynamoDB",
    "docdb": "AmazonDocDB",
    "neptune": "AmazonNeptune",
    "timestream": "AmazonTimestream",
    "dms": "AWSDatabaseMigrationSvc",
    "keyspaces": "AmazonMCS",
    "dsql": "AuroraDSQL",
    "fargate": "AmazonECS",
    "lambda": "AWSLambda",
    "sagemaker": "AmazonSageMaker",
}


def aws_service(service_name: str | None) -> str | None:
    """Short service for a billing service name or product code, or None if not one we price."""
    if not service_name:
        return None
    if service_name in AWS_SERVICES:
        return AWS_SERVICES[service_name]
    return next(
        (s for prefix, s in _AWS_SERVICE_PREFIXES.items() if service_name.startswith(prefix)),
        None,
    )


def strip_region(usage_type: str) -> str:
    """'USE2-InstanceUsage:db.m5.large' -> 'InstanceUsage:db.m5.large' (us-east-1 has none)."""
    prefix, sep, rest = usage_type.partition("-")
    return rest if sep and prefix in REGION_PREFIXES else usage_type


def aws_usage_key(service: str, usage_type: str, operation: str | None) -> str:
    return f"{service}|{strip_region(usage_type)}|{operation or ''}"


def aws_os(operating_system: str | None, pre_installed_sw: str | None = None) -> str:
    os_name = _AWS_OS.get(operating_system or "", operating_system or "Linux")
    sql = _AWS_SQL.get(pre_installed_sw or "")
    return f"{os_name} with {sql}" if sql else os_name


def aws_ec2_key(instance_type: str, operating_system: str, tenancy: str | None = "Shared") -> str:
    return f"ec2|{instance_type}|{operating_system}|{(tenancy or 'Shared').capitalize()}"


def aws_rds_key(instance_type: str, engine: str, deployment_option: str | None) -> str:
    return f"rds|{instance_type}|{engine}|{deployment_option or 'Single-AZ'}"


def aws_generic_key(service: str, instance_type: str) -> str:
    service = service.lower()
    if service == "opensearch":
        # Prices say "m7g.medium.search", usage types (ESInstance:m7g.medium) don't.
        instance_type = instance_type.removesuffix(".search")
    return f"{service}|{instance_type}"


def azure_os(product_name: str | None) -> str | None:
    if not product_name:
        return None
    return "Windows" if "windows" in product_name.lower() else "Linux"


# Azure names some services differently per source; keys and pools use the Cost Management
# Query API / Retail Prices name. FOCUS exports say "Azure Cache for Redis".
AZURE_SERVICE_ALIASES = {"Azure Cache for Redis": "Redis Cache"}


def azure_service(service_name: str | None) -> str | None:
    return AZURE_SERVICE_ALIASES.get(service_name or "", service_name)


def azure_sku_key(service_name: str, sku: str, os_name: str | None = None) -> str:
    service_name = azure_service(service_name) or ""
    parts = ["azure", service_name, sku]
    if service_name == "Virtual Machines" and os_name:
        parts.append(os_name)
    return "|".join(parts)


def azure_key(service_name: str, arm_sku_name: str, product_name: str | None = None) -> str:
    """Key for a Retail Prices item (OS taken from the product name)."""
    return azure_sku_key(service_name, arm_sku_name, azure_os(product_name))


def usage_sku_key(
    provider: str,
    service_name: str | None,
    instance_type: str | None,
    operating_system: str | None = None,
    tenancy: str | None = None,
    database_engine: str | None = None,
    deployment_option: str | None = None,
    usage_unit: str | None = None,
    usage_type: str | None = None,
    operation: str | None = None,
) -> str | None:
    """sku_key for a normalized usage row (the engine's join key into the price table)."""
    if provider == "azure":
        sku = instance_type or usage_unit
        return azure_sku_key(service_name or "", sku, operating_system) if sku else None
    service = aws_service(service_name)
    if service == "ec2" and instance_type:
        return aws_ec2_key(instance_type, operating_system or "Linux", tenancy)
    if service and service != "ec2" and usage_type:
        return aws_usage_key(service, usage_type, operation)
    # Rows collected before usage types were recorded.
    if service == "rds" and instance_type and database_engine:
        return aws_rds_key(instance_type, database_engine, deployment_option)
    if service == "fargate":
        return "fargate|vcpu"
    if service and instance_type:
        return aws_generic_key(service, instance_type)
    return None
