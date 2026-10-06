"""sku_key conventions, so usage rows and price rows can be matched by the engine."""

_AWS_SQL = {"SQL Std": "SQL Server Standard", "SQL Ent": "SQL Server Enterprise",
            "SQL Web": "SQL Server Web"}  # fmt: skip
_AWS_OS = {
    "Linux/UNIX": "Linux",
    "Red Hat Enterprise Linux": "RHEL",
    "Red Hat Enterprise Linux with HA": "RHEL with HA",
    "SUSE Linux": "SUSE",
    "Windows BYOL": "Windows BYOL",
}
# Cost Explorer / FOCUS service names -> short service used in keys and the price table.
AWS_SERVICES = {
    "Amazon Elastic Compute Cloud - Compute": "ec2",
    "Amazon Elastic Compute Cloud": "ec2",
    "AmazonEC2": "ec2",
    "Amazon Relational Database Service": "rds",
    "AmazonRDS": "rds",
    "Amazon ElastiCache": "elasticache",
    "AmazonElastiCache": "elasticache",
    "Amazon OpenSearch Service": "opensearch",
    "Amazon Redshift": "redshift",
    "Amazon MemoryDB": "memorydb",
    "Amazon DynamoDB": "dynamodb",
    "Amazon Elastic Container Service": "fargate",
    "AWS Lambda": "lambda",
}


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


def azure_sku_key(service_name: str, sku: str, os_name: str | None = None) -> str:
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
) -> str | None:
    """sku_key for a normalized usage row (the engine's join key into the price table)."""
    if provider == "azure":
        sku = instance_type or usage_unit
        return azure_sku_key(service_name or "", sku, operating_system) if sku else None
    service = AWS_SERVICES.get(service_name or "")
    if service == "ec2" and instance_type:
        return aws_ec2_key(instance_type, operating_system or "Linux", tenancy)
    if service == "rds" and instance_type and database_engine:
        return aws_rds_key(instance_type, database_engine, deployment_option)
    if service == "fargate":
        return "fargate|vcpu"
    if service and instance_type:
        return aws_generic_key(service, instance_type)
    return None
