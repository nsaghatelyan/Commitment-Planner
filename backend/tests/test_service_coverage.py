"""Regression tests: services that must reach the engine's commitment pools."""

from datetime import date

import numpy as np
import pytest

from app.collectors.aws.cost_explorer import CostExplorerBootstrap, commitment_category
from app.collectors.cache import CountingCaller
from app.collectors.types import CallStats
from app.engine.data import GROUP_COLUMNS, UsageGroup
from app.engine.pools import ri_pool
from app.pricing.keys import azure_key, usage_sku_key

DB_SP_SERVICES = [
    "Amazon DocumentDB (with MongoDB compatibility)",
    "Amazon Neptune",
    "Amazon Timestream",
    "AWS Database Migration Service",
    "Amazon Keyspaces (for Apache Cassandra)",
    "Amazon Aurora DSQL",
]


class FakeCE:
    """Records the SERVICE filter of every GetCostAndUsage request."""

    def __init__(self, services: list[str]) -> None:
        self.services = services
        self.detail: list[str] = []
        self.totals: list[list[str]] = []

    def get_dimension_values(self, **kw):
        values = self.services if kw["Dimension"] == "SERVICE" else ["On Demand Instances"]
        return {"DimensionValues": [{"Value": v} for v in values]}

    def get_cost_and_usage(self, **kw):
        groups = [g["Key"] for g in kw["GroupBy"]]
        f = kw["Filter"]
        if groups == ["USAGE_TYPE", "OPERATION"]:
            svc = next(
                e["Dimensions"]["Values"][0]
                for e in f["And"]
                if e.get("Dimensions", {}).get("Key") == "SERVICE"
            )
            self.detail.append(svc)
        else:
            self.totals.append(f["Dimensions"]["Values"])
        return {"ResultsByTime": []}


@pytest.mark.parametrize("name", DB_SP_SERVICES)
def test_database_savings_plan_services_are_commitment_eligible(name):
    assert commitment_category(name) in ("Databases", "Migration and Transfer")


def test_name_variants_and_non_eligible_services():
    assert commitment_category("Amazon SageMaker AI") == "AI and Machine Learning"
    assert commitment_category("Amazon MemoryDB Service") == "Databases"
    assert commitment_category("Amazon Simple Storage Service") is None
    assert commitment_category("Tax") is None


def test_backfill_fetches_database_services_at_usage_type_detail():
    """Without usage types the Database SP can't price these services; they must not be
    lumped into the per-service totals query."""
    services = [*DB_SP_SERVICES, "Amazon SageMaker AI", "Amazon Simple Storage Service"]
    ce = FakeCE(services)
    CostExplorerBootstrap(ce, CountingCaller(CallStats()), "111111111111").collect(
        date(2026, 9, 1), date(2026, 9, 2)
    )
    assert sorted(ce.detail) == sorted([*DB_SP_SERVICES, "Amazon SageMaker AI"])
    assert ce.totals == [["Amazon Simple Storage Service"]]


def _group(**attrs) -> UsageGroup:
    z = np.zeros(24)
    return UsageGroup({c: attrs.get(c) for c in GROUP_COLUMNS}, "od", None, z, z, z)


@pytest.mark.parametrize("name", ["Redis Cache", "Azure Cache for Redis"])
def test_azure_redis_is_reservable_under_either_name(name):
    g = _group(
        provider="azure",
        service_name=name,
        region="eastus",
        instance_type="P1",
        usage_unit="1 Hour",
    )
    pool = ri_pool(g)
    assert pool is not None and pool.service == "Redis Cache" and pool.measure == "qty"
    # Usage from the Query API and from a FOCUS export price against the same Retail Prices row.
    key = usage_sku_key("azure", name, "P1", usage_unit="1 Hour")
    assert key == azure_key("Redis Cache", "P1") == "azure|Redis Cache|P1"
