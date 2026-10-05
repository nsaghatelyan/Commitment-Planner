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


def aws_os(operating_system: str | None, pre_installed_sw: str | None = None) -> str:
    os_name = _AWS_OS.get(operating_system or "", operating_system or "Linux")
    sql = _AWS_SQL.get(pre_installed_sw or "")
    return f"{os_name} with {sql}" if sql else os_name


def aws_ec2_key(instance_type: str, operating_system: str, tenancy: str | None = "Shared") -> str:
    return f"ec2|{instance_type}|{operating_system}|{(tenancy or 'Shared').capitalize()}"


def aws_rds_key(instance_type: str, engine: str, deployment_option: str | None) -> str:
    return f"rds|{instance_type}|{engine}|{deployment_option or 'Single-AZ'}"


def aws_generic_key(service: str, instance_type: str) -> str:
    return f"{service.lower()}|{instance_type}"


def azure_os(product_name: str | None) -> str | None:
    if not product_name:
        return None
    return "Windows" if "windows" in product_name.lower() else "Linux"


def azure_key(service_name: str, arm_sku_name: str, product_name: str | None = None) -> str:
    os_name = azure_os(product_name) if service_name == "Virtual Machines" else None
    parts = ["azure", service_name, arm_sku_name] + ([os_name] if os_name else [])
    return "|".join(parts)
