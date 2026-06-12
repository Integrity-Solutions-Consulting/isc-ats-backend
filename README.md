# ISC ATS — Backend

Applicant Tracking System backend. **FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL**.

## Architecture — Screaming + pragmatic Clean

Top-level folders under `app/modules/` are **bounded contexts**, mapped 1:1 to the 7 database schemas (`auth`, `org`, `recruitment`, `talent`, `comms`, `storage`, `ai`). The folder structure screams *what the system does*, not *what framework it uses*.

Each module owns its layers internally:

```
app/modules/<context>/
├── api/             # HTTP routes + Pydantic schemas (transport)
├── application/     # use cases / services (orchestration)
├── domain/          # entities, value objects, interfaces (only where it pays)
└── infrastructure/  # SQLAlchemy models, repositories, external clients
```

### Pragmatic rule (the key decision)

Full ports & adapters (interface in `domain/` + implementation in `infrastructure/`) **only where it pays**:

- **Integration seams**: `ai/` (Claude API), `storage/` (MinIO), `comms/` (MS Graph + email)
- **Complex domain logic**: `recruitment/` pipeline stage transitions, matching

**Thin layer** (service + repository, the ORM *is* the model, no domain entities/mappers) for plain CRUD catalogs in `org/` (parameters, departments, contacts...).

### Shared & core

- `app/core/` — config, async DB session, JWT/hashing, dependency injection
- `app/shared/` — audit mixins, soft-delete, base repository, pagination

## Stack

| Concern        | Choice                          |
| -------------- | ------------------------------- |
| Runtime        | Python 3.12, FastAPI            |
| Package manager| uv                              |
| ORM            | SQLAlchemy 2.0 (async, asyncpg) |
| Migrations     | Alembic                         |
| Validation     | Pydantic v2 + pydantic-settings |
| Vector search  | pgvector (`cv_embedding`)       |

## Getting started

```bash
uv sync                      # install dependencies
cp .env.example .env         # configure environment
uv run alembic upgrade head  # apply migrations
uv run uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs

## Database

The schema is the source of truth: `docs/database/schema.sql` (29 tables, 7 schemas).
`org.parameters` is a polymorphic catalog referenced by almost every lookup FK —
seeding it is a prerequisite for everything else.
