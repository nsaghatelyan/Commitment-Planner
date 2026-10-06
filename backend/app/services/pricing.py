"""Upsert public prices into the shared price table."""

from collections.abc import Iterable

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


AWS_PRICE_SERVICES = {
    "Amazon Elastic Compute Cloud - Compute": "AmazonEC2",
    "Amazon Elastic Compute Cloud": "AmazonEC2",
    "Amazon Relational Database Service": "AmazonRDS",
    "Amazon ElastiCache": "AmazonElastiCache",
    "Amazon OpenSearch Service": "AmazonES",
}


def sync_prices_for_usage(session: Session, store, conn, collector) -> int:
    """Fetch public prices only for the (region, instance type) pairs this connection uses.

    AWS uses the collector's own session (profile or default credentials; the Pricing and
    Savings Plans rate APIs are free). Azure uses the public Retail Prices API."""
    from app.collectors.cache import CountingCaller
    from app.timeutil import utc_today
    from app.usage.query import connect

    today = utc_today()
    con = connect(store, str(conn.tenant_id))
    rows = con.execute(
        """SELECT DISTINCT service_name, region, instance_type FROM usage
           WHERE provider = ? AND instance_type IS NOT NULL AND region IS NOT NULL""",
        [conn.provider],
    ).fetchall()
    if not rows:
        return 0
    if conn.provider == "azure":
        from app.collectors.azure.http import AzureHttp
        from app.pricing.azure import parse_item, retail_items

        call = CountingCaller(collector.stats)
        http = AzureHttp(None)
        records = []
        for service, region, sku in rows:
            odata = f"armRegionName eq '{region}' and armSkuName eq '{sku}'"
            for item in retail_items(http, call, odata):
                records += parse_item(item, today)
        return upsert_prices(session, records)

    from app.pricing import aws

    if getattr(collector, "role_arn", None):
        # Client roles don't grant pricing:GetProducts; public prices use the tool's own
        # credentials.
        import boto3

        def client(service: str, region: str):
            return boto3.client(service, region_name=region)
    else:
        client = collector.client
    pricing = client("pricing", "us-east-1")
    total = 0
    ec2_by_region: dict[str, set[str]] = {}
    db_by_region: dict[str, set[str]] = {}
    for service, region, itype in rows:
        code = AWS_PRICE_SERVICES.get(service)
        if not code:
            continue
        if code == "AmazonEC2":
            ec2_by_region.setdefault(region, set()).add(itype)
        if code == "AmazonES" and not itype.endswith(".search"):
            itype += ".search"  # the price APIs' name for it
        if code in ("AmazonRDS", "AmazonES"):
            db_by_region.setdefault(region, set()).add(itype)
        total += upsert_prices(
            session,
            aws.on_demand_and_ri_prices(
                pricing, collector.call, code, [region], today, {"instanceType": itype}
            ),
        )
    if ec2_by_region:
        savingsplans = client("savingsplans", "us-east-1")
        types = sorted({t for ts in ec2_by_region.values() for t in ts})
        total += upsert_prices(
            session,
            aws.savings_plan_rates(
                savingsplans, collector.call, sorted(ec2_by_region), today, instance_types=types
            ),
        )
    if db_by_region:
        savingsplans = client("savingsplans", "us-east-1")
        types = sorted({t for ts in db_by_region.values() for t in ts})
        total += upsert_prices(
            session,
            aws.database_savings_plan_rates(
                savingsplans, collector.call, sorted(db_by_region), today, instance_types=types
            ),
        )
    return total
