"""Upsert public prices into the shared price table."""

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.engine.pools import AWS_DATABASE_SP_SERVICES as DATABASE_SP_SERVICES
from app.models import Price
from app.pricing.types import PriceRecord

BATCH = 1000


def upsert_prices(session: Session, records: Iterable[PriceRecord]) -> int:
    count = 0
    batch: list[dict] = []
    for r in records:
        batch.append(
            {
                "provider": r.provider,
                "service": r.service,
                "sku_key": r.sku_key,
                "region": r.region,
                "pricing_model": r.pricing_model,
                "term_months": r.term_months,
                "payment_option": r.payment_option,
                "unit": r.unit,
                "price_per_unit": r.price_per_unit,
                "currency": r.currency,
                "effective_from": r.effective_from,
                "attributes": r.attributes,
            }
        )
        if len(batch) >= BATCH:
            count += _flush(session, batch)
            # Commit per batch: a full catalog pull takes minutes, and holding its row locks
            # that long blocks every collection that syncs overlapping prices.
            session.commit()
            batch = []
    if batch:
        count += _flush(session, batch)
    session.commit()
    return count


def _flush(session: Session, batch: list[dict]) -> int:
    # Keep the last record per identity so one statement never updates a row twice.
    unique = {
        (
            b["provider"],
            b["sku_key"],
            b["region"],
            b["pricing_model"],
            b["term_months"],
            b["payment_option"],
            b["effective_from"],
        ): b
        for b in batch
    }
    stmt = insert(Price).values(list(unique.values()))
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_price_identity",
            set_={
                "price_per_unit": stmt.excluded.price_per_unit,
                "unit": stmt.excluded.unit,
                "currency": stmt.excluded.currency,
                "service": stmt.excluded.service,
                "attributes": stmt.excluded.attributes,
            },
        )
    )
    return len(unique)


def sync_prices_for_usage(session: Session, store, conn, collector) -> int:
    """Fetch public prices only for what this connection uses: EC2 by instance type, every
    other AWS service by the exact usage types it was billed under.

    AWS uses the collector's own session (profile or default credentials; the Pricing and
    Savings Plans rate APIs are free). Azure uses the public Retail Prices API."""
    from app.collectors.cache import CountingCaller
    from app.timeutil import utc_today
    from app.usage.query import connect

    today = utc_today()
    con = connect(store, str(conn.tenant_id))
    if conn.provider == "azure":
        from app.collectors.azure.http import AzureHttp
        from app.pricing.azure import parse_item, retail_items

        rows = con.execute(
            """SELECT DISTINCT region, instance_type FROM usage
               WHERE provider = 'azure' AND instance_type IS NOT NULL AND region IS NOT NULL"""
        ).fetchall()
        call = CountingCaller(collector.stats)
        http = AzureHttp(None)
        records = []
        for region, sku in rows:
            odata = f"armRegionName eq '{region}' and armSkuName eq '{sku}'"
            for item in retail_items(http, call, odata):
                records += parse_item(item, today)
        return upsert_prices(session, records)

    from app.pricing import aws
    from app.pricing.keys import AWS_SERVICE_CODES, aws_service

    has_usage_type = "usage_type" in {d[0] for d in con.execute("DESCRIBE usage").fetchall()}
    rows = con.execute(
        f"""SELECT DISTINCT service_name, region, instance_type,
                   {"usage_type" if has_usage_type else "NULL"} AS usage_type
            FROM usage
            WHERE provider = 'aws' AND region IS NOT NULL AND charge_category = 'Usage'"""
    ).fetchall()
    if not rows:
        return 0
    if getattr(collector, "role_arn", None):
        # Client roles don't grant pricing:GetProducts; public prices use the tool's own
        # credentials.
        import boto3

        def client(service: str, region: str):
            return boto3.client(service, region_name=region)
    else:
        client = collector.client
    pricing = client("pricing", "us-east-1")
    savingsplans = client("savingsplans", "us-east-1")
    plan_of = {s: "Database" for s in DATABASE_SP_SERVICES}
    plan_of.update(fargate="Compute", sagemaker="SageMaker", **{"lambda": "Compute"})
    total = 0
    ec2_by_region: dict[str, set[str]] = defaultdict(set)
    by_plan: dict[str, tuple[set[str], set[str]]] = defaultdict(lambda: (set(), set()))
    for service_name, region, itype, usage_type in rows:
        service = aws_service(service_name)
        if service == "ec2" and itype:
            if itype not in ec2_by_region[region]:
                ec2_by_region[region].add(itype)
                total += upsert_prices(
                    session,
                    aws.on_demand_and_ri_prices(
                        pricing, collector.call, "AmazonEC2", [region], today,
                        {"instanceType": itype},
                    ),
                )  # fmt: skip
            continue
        if not service or service == "ec2" or not usage_type:
            continue
        total += upsert_prices(
            session,
            aws.on_demand_and_ri_prices(
                pricing, collector.call, AWS_SERVICE_CODES[service], [region], today,
                {"usagetype": usage_type},
            ),
        )  # fmt: skip
        if service in plan_of:
            regions, usage_types = by_plan[plan_of[service]]
            regions.add(region)
            usage_types.add(usage_type)
    if ec2_by_region:
        types = sorted({t for ts in ec2_by_region.values() for t in ts})
        total += upsert_prices(
            session,
            aws.savings_plan_rates(
                savingsplans, collector.call, sorted(ec2_by_region), today, instance_types=types
            ),
        )
    for plan_type, (regions, usage_types) in by_plan.items():
        total += upsert_prices(
            session,
            aws.usage_savings_plan_rates(
                savingsplans, collector.call, plan_type, sorted(regions), today,
                usage_types=sorted(usage_types),
            ),
        )  # fmt: skip
    return total
