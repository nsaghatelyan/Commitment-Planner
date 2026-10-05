# Savings Tool

Hosted SaaS that pulls AWS and Azure usage, analyzes it, and recommends savings plans and reservations.

## Layout

```
backend/            Python 3.12, FastAPI
  app/api/          HTTP routes (only /health so far)
  app/models/       SQLAlchemy models (Postgres)
  app/collectors/   aws/ and azure/ usage + commitment collectors (stubs)
  app/engine/       recommendation engine over Parquet usage via DuckDB (stub)
  app/workers/      background jobs (arq on Redis, stubs)
  migrations/       Alembic migrations
  tests/
frontend/           Next.js (App Router, TypeScript)
infra/              CloudFormation for the client's read-only IAM role (with ExternalId)
docker-compose.yml  Postgres 16 and Redis 7 for local development
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
cd backend && pytest && ruff check . && ruff format --check .
cd frontend && npm run lint && npm run build
```

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
