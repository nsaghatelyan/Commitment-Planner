"""Load a synthetic tenant into Postgres and the usage store."""

import pyarrow.compute as pc
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CloudConnection, Tenant
from app.services.collection import upsert_commitments, upsert_utilization
from app.services.pricing import upsert_prices
from app.synthetic.generator import SyntheticTenant
from app.usage.storage import UsageStore


def seed(session: Session, tenant: SyntheticTenant, store: UsageStore) -> dict[str, int]:
    """Idempotent: re-running replaces the tenant's usage partitions and upserts the rest."""
    import uuid

    from sqlalchemy.dialects.postgresql import insert

    from app.models import CloudAccount

    tenant_id = uuid.UUID(tenant.tenant_id)
    row = session.get(Tenant, tenant_id)
    if row is None:
        session.add(Tenant(id=tenant_id, name=tenant.name, slug=f"synthetic-{tenant.profile}"))
        session.flush()

    counts = {"usage_rows": 0, "commitments": 0, "utilization": 0}
    for provider in ("aws", "azure"):
        part = tenant.usage.filter(pc.equal(tenant.usage.column("provider"), provider))
        counts["usage_rows"] += sum(store.write(tenant.tenant_id, provider, part).values())

    for sc in tenant.connections:
        conn = session.scalars(
            select(CloudConnection).where(
                CloudConnection.tenant_id == tenant_id, CloudConnection.name == sc.name
            )
        ).first()
        if conn is None:
            conn = CloudConnection(
                tenant_id=tenant_id,
                provider=sc.provider,
                name=sc.name,
                azure_agreement_type=sc.agreement_type,
                azure_billing_scope=sc.billing_scope,
                # Never picked up by collection workers.
                status="synthetic",
            )
            session.add(conn)
            session.flush()
        for acct in sc.accounts:
            stmt = insert(CloudAccount).values(
                tenant_id=tenant_id,
                cloud_connection_id=conn.id,
                provider=sc.provider,
                external_account_id=acct.id,
                name=acct.name,
                is_payer=acct.is_payer,
            )
            session.execute(stmt.on_conflict_do_nothing())
        session.flush()
        accounts = dict(
            session.execute(
                select(CloudAccount.external_account_id, CloudAccount.id).where(
                    CloudAccount.cloud_connection_id == conn.id
                )
            ).all()
        )
        commitments = [c for c in tenant.commitments if c.kind.startswith(sc.provider)]
        ids = upsert_commitments(session, conn, accounts, commitments)
        counts["commitments"] += len(commitments)
        counts["utilization"] += upsert_utilization(session, ids, commitments, tenant.utilization)
    session.commit()
    counts["prices"] = upsert_prices(session, tenant.prices)
    return counts
