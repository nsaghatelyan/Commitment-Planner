"""python -m app.synthetic [--profile small|medium|large|startup|all] [--seed-db] ..."""

import argparse
from datetime import date

import pyarrow.compute as pc

from app.config import get_settings
from app.synthetic.generator import PROFILES, generate
from app.usage.storage import UsageStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="all", choices=[*PROFILES, "all"])
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="exclusive end date")
    parser.add_argument("--days", type=int, default=None, help="override days of history")
    parser.add_argument("--out", default=None, help="usage storage root (default: settings)")
    parser.add_argument("--seed-db", action="store_true", help="also load Postgres")
    parser.add_argument(
        "--analyze", action="store_true", help="with --seed-db: also run the engine (balanced)"
    )
    args = parser.parse_args()

    store = UsageStore(args.out or get_settings().usage_storage_root)
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    for name in profiles:
        tenant = generate(name, end=args.end, days=args.days)
        if args.seed_db:
            from app.db import SessionLocal
            from app.synthetic.seed import seed

            with SessionLocal() as session:
                counts = seed(session, tenant, store)
                if args.analyze:
                    import uuid

                    from app.services.analysis import run_analysis

                    for profile in ("conservative", "aggressive", "balanced"):
                        run = run_analysis(
                            session, uuid.UUID(tenant.tenant_id), store=store, risk_profile=profile
                        )
                        counts[f"analysis_{profile}"] = run.status
        else:
            counts = {"usage_rows": 0}
            for provider in ("aws", "azure"):
                part = tenant.usage.filter(pc.equal(tenant.usage.column("provider"), provider))
                counts["usage_rows"] += sum(store.write(tenant.tenant_id, provider, part).values())
        print(f"{name}: tenant {tenant.tenant_id} {counts}")


if __name__ == "__main__":
    main()
