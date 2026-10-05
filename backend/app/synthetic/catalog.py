"""Approximate public on-demand prices (USD/hour, Linux) used by the synthetic generator."""

# instance type -> (vCPU, on-demand $/hour)
AWS_EC2 = {
    "t3.medium": (2, 0.0416),
    "m4.xlarge": (4, 0.20),
    "m5.large": (2, 0.096),
    "m5.xlarge": (4, 0.192),
    "m5.2xlarge": (8, 0.384),
    "m6i.large": (2, 0.096),
    "m6i.xlarge": (4, 0.192),
    "m6i.2xlarge": (8, 0.384),
    "c5.xlarge": (4, 0.17),
    "c5.2xlarge": (8, 0.34),
    "r5.large": (2, 0.126),
    "r5.xlarge": (4, 0.252),
    "r6g.xlarge": (4, 0.2016),
}
# Windows license uplift per vCPU-hour.
AWS_WINDOWS_PER_VCPU = 0.046

# (instance class, engine, deployment) -> $/hour
AWS_RDS = {
    ("db.m5.large", "MySQL", "Single-AZ"): 0.171,
    ("db.r5.large", "PostgreSQL", "Single-AZ"): 0.25,
    ("db.r5.xlarge", "PostgreSQL", "Multi-AZ"): 1.00,
    ("db.r5.xlarge", "SQL Server SE", "Multi-AZ"): 3.98,
    ("db.r5.xlarge", "SQL Server SE", "Single-AZ"): 1.99,
}

AZURE_VM = {
    "Standard_B2s": (2, 0.0416),
    "Standard_D2s_v3": (2, 0.096),
    "Standard_D4s_v3": (4, 0.192),
    "Standard_D8s_v3": (8, 0.384),
    "Standard_D4s_v5": (4, 0.192),
    "Standard_E4s_v3": (4, 0.252),
    "Standard_E8s_v3": (8, 0.504),
    "Standard_F4s_v2": (4, 0.169),
}
AZURE_WINDOWS_PER_VCPU = 0.046

AWS_SIZE_FACTORS = {
    "nano": 0.25, "micro": 0.5, "small": 1, "medium": 2, "large": 4, "xlarge": 8,
    "2xlarge": 16, "4xlarge": 32, "8xlarge": 64, "12xlarge": 96, "16xlarge": 128,
    "24xlarge": 192,
}  # fmt: skip

# Discount off on-demand, by (provider, commitment, term months).
DISCOUNTS = {
    ("aws", "compute_sp", 12): 0.27,
    ("aws", "compute_sp", 36): 0.48,
    ("aws", "ec2_sp", 12): 0.33,
    ("aws", "ec2_sp", 36): 0.55,
    ("aws", "ri", 12): 0.38,
    ("aws", "ri", 36): 0.58,
    ("azure", "sp", 12): 0.28,
    ("azure", "sp", 36): 0.48,
    ("azure", "ri", 12): 0.40,
    ("azure", "ri", 36): 0.60,
}
SPOT_DISCOUNT = 0.68


def aws_size_factor(instance_type: str) -> float:
    return AWS_SIZE_FACTORS.get(instance_type.rsplit(".", 1)[-1], 1)
