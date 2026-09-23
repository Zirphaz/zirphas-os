# Validation record

Recorded on September 18, 2026, using Python 3.12.10 and PostgreSQL 17.6 on Windows.

## Results

| Check | Result |
| --- | --- |
| Automated suite | 68 tests passed |
| PostgreSQL integration | 5 concurrency and integrity scenarios passed |
| Migrations | Applied from an empty database successfully |
| State transitions | Valid and invalid paths covered |
| Estimate approval | Previous approval invalidated after an estimate change |
| Inventory | Concurrent consumption protected by database locking |
| Payments | Idempotency and outstanding-balance rules covered |
| Customer access | Tracking-token hashing and replacement covered |
| Authorization | Administrator, receptionist, technician, and customer boundaries covered |

## PostgreSQL scenarios

The integration suite exercised concurrent part consumption, repeated payments with the same idempotency key, two payments racing for the remaining balance, estimate changes after approval, and financial-history consistency. Each test used PostgreSQL rather than an in-memory substitute.

## Commands

```console
alembic upgrade head
pytest -q
```

The CI workflow provisions PostgreSQL, applies the migrations, and executes the suite for pushes and pull requests.

## Scope of this record

These results validate the listed behaviors in the tested environment. The Docker image and browser rendering were not part of this local record. Load testing, backup restoration, security review, and production observability require a representative deployment environment.
