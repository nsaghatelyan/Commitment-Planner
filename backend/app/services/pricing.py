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
