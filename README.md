# Savings Tool

Hosted SaaS that pulls AWS and Azure usage, analyzes it, and recommends savings plans and reservations.

## Layout

```
backend/            Python 3.12, FastAPI
  app/api/          HTTP routes (only /health so far)
  app/models/       SQLAlchemy models (Postgres)
  app/collectors/   aws/ and azure/ collectors: usage, commitments, utilization, native recs
  app/usage/        normalized usage schema, Parquet store, FOCUS / CUR 2.0 normalizers
  app/pricing/      AWS Pricing API + SP offering rates, Azure Retail Prices
  app/services/     collection run orchestration, price upserts
  app/synthetic/    synthetic tenants (small, medium, large, startup)
  app/engine/       recommendation engine over Parquet usage via DuckDB (stub)
  app/workers/      background jobs (arq on Redis): collect_usage, refresh_prices
  migrations/       Alembic migrations
  tests/            unit tests with mocked AWS/Azure responses (tests/fixtures/)
frontend/           Next.js (App Router, TypeScript)
infra/              CloudFormation for the client's read-only IAM role (with ExternalId)
docker-compose.yml  Postgres 16 (localhost:5433) and Redis 7 (localhost:6380) for local development
```

## Prerequisites

- Python 3.12
- Node.js 20+
- Docker (for Postgres and Redis)

## Setup

Start Postgres and Redis:

```sh
docker compose up -d
```

They are published on 5433 and 6380 rather than the default ports so they don't collide with a
locally installed Postgres or Redis.

Backend:

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload          # http://localhost:8000/health
arq app.workers.main.WorkerSettings    # background worker (separate shell)
```

Frontend:

```sh
cd frontend
npm install
cp .env.example .env.local
npm run dev                            # http://localhost:3000
```

## Tests and lint

```sh
cd backend && pytest && ruff check . && ruff format --check .   # DB tests need docker compose up
cd frontend && npm run lint && npm run build
```

## Usage data

Normalized usage (FOCUS-aligned columns, see `backend/app/usage/schema.py`) is written as
Parquet under `USAGE_STORAGE_ROOT` (local path in dev, `s3://...` in prod):

```
usage_hourly/tenant=<id>/provider=<aws|azure>/date=YYYY-MM-DD/part-0.parquet
```

Each write replaces whole day partitions. Query it with DuckDB (`hive_partitioning = true`);
`app.usage.query.connect(store, tenant_id)` gives a connection with a `usage` view.
Native provider recommendations are saved next to it under `native_recommendations/`.

## Collection

`collect_usage(cloud_connection_id)` (worker job, or `app.services.collection.run_collection`):

1. Tests the connection and syncs accounts / subscriptions.
2. Reads the client's billing export (AWS Data Exports FOCUS 1.0 or CUR 2.0; Azure Cost
   Management FOCUS export) - the primary source.
3. Backfills up to 13 months from the API (Cost Explorer / Cost Management Query) for days
   the export doesn't cover yet. Cost Explorer calls cost $0.01 each, so responses are cached
   in `API_CACHE_DIR` and every call is counted in `collection_run.api_calls`.
4. Inventories existing savings plans and reservations, their daily utilization, and the
   provider's own purchase recommendations.

### AWS onboarding

The client deploys `infra/client-readonly-role.yaml` in their payer account (see below). Set
`CreateDataExport=true` (in us-east-1) to also create an hourly Parquet Data Export and bucket;
the stack outputs give `aws_role_arn`, `aws_export_bucket` and `aws_export_prefix`.

### Azure onboarding

The tool uses one multi-tenant Entra app that signs in with a certificate
(`AZURE_APP_CLIENT_ID`, `AZURE_APP_CERTIFICATE_PATH`). The client consents to it in their
tenant and grants read access at their billing scope, which depends on the agreement type
(`azure_agreement_type`):

| type | `azure_billing_scope` |
|------|------------------------|
| ea   | `/providers/Microsoft.Billing/billingAccounts/<enrollment number>` |
| mca  | `/providers/Microsoft.Billing/billingAccounts/<id>/billingProfiles/<profile id>` |
| payg | `/subscriptions/<subscription id>` |
| csp  | `/providers/Microsoft.Billing/billingAccounts/<id>/customers/<customer id>` |

Plus Reservations Reader and Savings plan Reader, and Storage Blob Data Reader on the FOCUS
export container (`azure_export_container`, e.g. `https://acct.blob.core.windows.net/costs/exports`).

## Synthetic data

For development without a cloud account:

```sh
cd backend
python -m app.synthetic                     # all profiles, Parquet only
python -m app.synthetic --profile large --seed-db   # also tenant, commitments, prices in Postgres
```

Profiles: `small` (AWS only), `medium` (Azure EA), `large` (AWS org + Azure MCA with layered
SPs and RIs, an RI stranded by a migration, Spot, Windows, SQL Server RDS), `startup`
(AWS + Azure PAYG, 21 days, no commitments). Output is deterministic.

## Pricing

`refresh_prices` (worker job) loads public prices into the `price` table: AWS Pricing API
(on-demand and RI) and Savings Plans offering rates, using the tool's own AWS credentials, and
the Azure Retail Prices API (no auth). `sku_key` conventions are in `app/pricing/keys.py`.

## Migrations

After changing models in `backend/app/models/`, with Postgres running:

```sh
cd backend
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

To see the SQL without a database: `alembic upgrade head --sql`.

## Client AWS onboarding

`infra/client-readonly-role.yaml` is deployed by the client in their payer account. It creates a role
that only the tool's AWS account can assume, and only with the client's ExternalId. It grants read access
to Cost Explorer, Savings Plans and reservation APIs, Organizations account listing, and optionally the
client's CUR / Data Exports bucket. The role ARN from the stack output is entered in the tool.

```sh
aws cloudformation deploy \
  --template-file infra/client-readonly-role.yaml \
  --stack-name savings-tool-readonly \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides ToolAccountId=<tool account id> ExternalId=<issued external id>
```
