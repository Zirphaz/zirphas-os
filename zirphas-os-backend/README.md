# ZirphasOS API

FastAPI service for the ZirphasOS repair-management workflow. It combines a relational domain model, role-based access control, a repair-order state machine, transactional inventory changes, and financial records.

## Stack

- Python 3.12 and FastAPI
- PostgreSQL 17 and SQLAlchemy 2
- Alembic migrations
- Pydantic validation
- JWT authentication and Argon2 password hashing
- Pytest and HTTPX
- Docker Compose

## Local setup with Docker

Create the environment file:

```powershell
Copy-Item .env.example .env
```

```sh
cp .env.example .env
```

Set unique values for `POSTGRES_PASSWORD` and `SECRET_KEY`. Then start PostgreSQL and the API:

```console
docker compose up -d db
docker compose up --build -d api
docker compose exec api python -m app.cli create-admin
```

Open <http://localhost:8000/docs>. The API applies pending migrations during container startup. No administrator credentials are hard-coded.

To stop the environment while preserving the database volume:

```console
docker compose down
```

## Local setup without Docker

Use a PostgreSQL instance dedicated to development and set `DATABASE_URL` in `.env`.

```console
python -m venv .venv
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

On Windows PowerShell, activate the environment with `.\.venv\Scripts\Activate.ps1`. On Linux or macOS, use `source .venv/bin/activate`.

## Domain rules

- Only allowed order-state transitions are accepted.
- A changed estimate requires a new customer decision.
- Inventory is checked and consumed transactionally when the repair is completed.
- Duplicate payment requests use an idempotency key.
- A device can only be delivered after full payment.
- Cost, commission, and margin data is visible only to administrators.
- Customer tracking tokens are stored as hashes and can be revoked by replacement.

## Project layout

```text
app/
  api/          routes, dependencies, and authorization
  core/         configuration, security, and state transitions
  models/       SQLAlchemy entities
  schemas/      Pydantic request and response contracts
  services/     business rules and transactions
alembic/        versioned database migrations
tests/          domain, API, and PostgreSQL concurrency tests
docs/           validation notes and project assets
```

## Tests

Create a separate test database and provide its URL through the test environment configuration. Then run:

```console
pytest -q
```

The GitHub Actions workflow provisions PostgreSQL, applies migrations, and runs the same suite. Five integration cases verify concurrent part consumption, idempotent payments, and related database invariants.

## API areas

- `/api/v1/auth` — staff authentication
- `/api/v1/users` — user administration
- `/api/v1/clients` — customers and devices
- `/api/v1/orders` — repair-order workflow
- `/api/v1/inventory` — parts and stock
- `/api/v1/dashboard` — administrative indicators
- `/api/v1/public` — token-based customer tracking
- `/api/v1/attachments` — controlled file attachments

Swagger documents the concrete request and response schemas at runtime.

## Production considerations

The Compose configuration is intended for local evaluation. A production deployment still needs managed secrets, TLS termination, backups and restore tests, monitoring, controlled file storage, rate limiting, and an operational process for database migrations. Review [VALIDATION.md](docs/VALIDATION.md) for the checks already completed and their limitations.
