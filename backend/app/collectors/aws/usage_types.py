"""Decode Cost Explorer usage types / operations into region, instance type, OS and tenancy."""

REGION_PREFIXES = {
    "USE1": "us-east-1", "USE2": "us-east-2", "USW1": "us-west-1", "USW2": "us-west-2",
    "CAN1": "ca-central-1", "CAW1": "ca-west-1", "SAE1": "sa-east-1", "MXC1": "mx-central-1",
    "EUC1": "eu-central-1", "EUC2": "eu-central-2", "EUW1": "eu-west-1", "EUW2": "eu-west-2",
    "EUW3": "eu-west-3", "EUN1": "eu-north-1", "EUS1": "eu-south-1", "EUS2": "eu-south-2",
    "APN1": "ap-northeast-1", "APN2": "ap-northeast-2", "APN3": "ap-northeast-3",
    "APS1": "ap-southeast-1", "APS2": "ap-southeast-2", "APS3": "ap-south-1",
    "APS4": "ap-southeast-3", "APS5": "ap-south-2", "APS6": "ap-southeast-4",
    "APS7": "ap-southeast-5", "APE1": "ap-east-1", "MES1": "me-south-1", "MEC1": "me-central-1",
    "AFS1": "af-south-1", "ILC1": "il-central-1", "UGW1": "us-gov-west-1", "UGE1": "us-gov-east-1",
    # eu-west-1 kept its original prefix ("EU-BoxUsage:m5.large").
    "EU": "eu-west-1",
}  # fmt: skip

# EC2 RunInstances operation suffix -> operating system / license.
EC2_PLATFORMS = {
    "": "Linux",
    "0002": "Windows",
    "0006": "Windows with SQL Server Standard",
    "0102": "Windows with SQL Server Enterprise",
    "0202": "Windows with SQL Server Web",
    "0800": "Windows BYOL",
    "0010": "RHEL",
    "1010": "RHEL with HA",
    "000g": "SUSE",
    "0004": "Linux with SQL Server Standard",
    "0100": "Linux with SQL Server Enterprise",
    "0200": "Linux with SQL Server Web",
    "00g0": "Ubuntu Pro",
}

_TENANCY = {"BoxUsage": "Shared", "SpotUsage": "Shared", "DedicatedUsage": "Dedicated",
            "HostUsage": "Host", "ReservedHostUsage": "Host"}  # fmt: skip


def parse_usage_type(usage_type: str) -> dict[str, str | None]:
    """'USE2-BoxUsage:m5.large' -> region us-east-2, instance_type m5.large, tenancy Shared."""
    prefix, sep, rest = usage_type.partition("-")
    if sep and prefix in REGION_PREFIXES:
        region = REGION_PREFIXES[prefix]
    else:
        # Usage types without a region prefix are us-east-1.
        region, rest = "us-east-1", usage_type
    kind, _, detail = rest.partition(":")
    instance_type = detail if "." in detail else None
    return {"region": region, "usage_kind": kind, "instance_type": instance_type,
            "tenancy": _TENANCY.get(kind)}  # fmt: skip


def operating_system(operation: str | None) -> str | None:
    if not operation or not operation.startswith("RunInstances"):
        return None
    _, _, code = operation.partition(":")
    return EC2_PLATFORMS.get(code, f"RunInstances:{code}")


# RDS CreateDBInstance operation suffix -> engine.
RDS_ENGINES = {
    "0002": "MySQL",
    "0003": "Oracle SE1 (BYOL)",
    "0004": "Oracle SE (BYOL)",
    "0005": "Oracle EE (BYOL)",
    "0006": "Oracle SE1 (LI)",
    "0008": "SQL Server SE (BYOL)",
    "0009": "SQL Server EE (BYOL)",
    "0010": "SQL Server Express",
    "0011": "SQL Server Web",
    "0012": "SQL Server SE",
    "0014": "PostgreSQL",
    "0015": "SQL Server EE",
    "0016": "Aurora MySQL",
    "0018": "MariaDB",
    "0019": "Oracle SE2 (BYOL)",
    "0020": "Oracle SE2 (LI)",
    "0021": "Aurora PostgreSQL",
}


def database_engine(operation: str | None) -> str | None:
    if not operation or not operation.startswith("CreateDBInstance"):
        return None
    _, _, code = operation.partition(":")
    return RDS_ENGINES.get(code, f"CreateDBInstance:{code}")


def deployment_option(usage_kind: str | None) -> str | None:
    if not usage_kind:
        return None
    if usage_kind.startswith("Multi-AZ"):
        return "Multi-AZ"
    if usage_kind.startswith("InstanceUsage"):
        return "Single-AZ"
    return None


# Normalization factors per size (RI size flexibility and normalized units).
SIZE_FACTORS = {
    "nano": 0.25, "micro": 0.5, "small": 1, "medium": 2, "large": 4, "xlarge": 8,
    "2xlarge": 16, "3xlarge": 24, "4xlarge": 32, "6xlarge": 48, "8xlarge": 64,
    "9xlarge": 72, "10xlarge": 80, "12xlarge": 96, "16xlarge": 128, "18xlarge": 144,
    "24xlarge": 192, "32xlarge": 256, "48xlarge": 384,
}  # fmt: skip


def aws_size_factor(instance_type: str) -> float:
    return SIZE_FACTORS.get(instance_type.rsplit(".", 1)[-1], 1)
