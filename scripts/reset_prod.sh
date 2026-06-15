#!/bin/sh
# Reset the database from scratch and reseed system parameters.
#
# Wipes EVERYTHING by dropping the 7 bounded-context schemas with CASCADE plus
# the Alembic version table, then rebuilds the schema and re-runs all seed
# migrations (`alembic upgrade head`), then bootstraps the RBAC baseline + admin
# user.
#
# Why DROP SCHEMA ... CASCADE instead of `alembic downgrade base`: the structural
# seed migration's downgrade issues a plain DELETE on org.parameters, which fails
# with a foreign-key violation whenever dependent rows exist (e.g. vacancies still
# reference vacancy_status). That aborts the whole downgrade chain before it can
# reach the base migration's own clean schema drop. Dropping the schemas directly
# sidesteps every FK and is what the base migration's downgrade would do anyway.
#
# DESTRUCTIVE — deletes all data. Use only on environments with throwaway/test
# data. Never run against a database with real users you need to keep. Take a
# backup first if in doubt:  pg_dump "<sync DSN>" > backup.sql
#
# Usage (inside the backend service shell):
#     sh scripts/reset_prod.sh --email admin@integritysolutions.com.ec
#     sh scripts/reset_prod.sh --email admin@isc.com --yes              # skip confirmation
#     sh scripts/reset_prod.sh --email admin@isc.com --password 'pw' -y # fully non-interactive
#
# Do NOT run scripts/seed_dev_data.py afterwards in production — that seeds fake
# demo data (banks, candidates). Business catalogs are loaded by HR via the UI.
set -e

# Prefer the venv binaries already on PATH (both prod and dev images put
# /app/.venv/bin first). Fall back to uv only if they're missing, and point its
# cache at a writable dir — the container user (appuser) has no writable HOME, so
# a bare `uv run` fails with "Failed to initialize cache at ~/.cache/uv".
if command -v alembic >/dev/null 2>&1 && command -v python >/dev/null 2>&1; then
  ALEMBIC="alembic"
  PY="python"
else
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
  ALEMBIC="uv run alembic"
  PY="uv run python"
fi

ADMIN_EMAIL=""
ADMIN_PASSWORD=""
ASSUME_YES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --email) ADMIN_EMAIL="$2"; shift 2 ;;
    --password) ADMIN_PASSWORD="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES="1"; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [ -z "$ADMIN_EMAIL" ]; then
  echo "ERROR: --email <admin email> is required"
  exit 1
fi

if [ -z "$ASSUME_YES" ]; then
  printf "This will DROP ALL DATA and rebuild from scratch. Type RESET to continue: "
  read -r confirm
  [ "$confirm" = "RESET" ] || { echo "Aborted."; exit 1; }
fi

echo "==> [1/3] Dropping all schemas (DROP SCHEMA ... CASCADE + alembic_version)..."
$PY - <<'PYEOF'
import asyncio

from sqlalchemy import text

from app.core.database import engine

# The 7 bounded-context schemas (must match alembic/env.py SCHEMAS).
SCHEMAS = ["auth", "org", "recruitment", "talent", "comms", "storage", "ai"]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS " + ", ".join(SCHEMAS) + " CASCADE"))
        # version_table_schema is "public" (see alembic/env.py). Dropping it is
        # required, otherwise `alembic upgrade head` thinks it is already current
        # and creates nothing.
        await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    await engine.dispose()


asyncio.run(main())
print("    schemas + alembic_version dropped")
PYEOF

echo "==> [2/3] Rebuilding schema + seeding system parameters (alembic upgrade head)..."
$ALEMBIC upgrade head

echo "==> [3/3] Bootstrapping RBAC + admin user ($ADMIN_EMAIL)..."
if [ -n "$ADMIN_PASSWORD" ]; then
  $PY scripts/bootstrap_admin.py --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD"
else
  $PY scripts/bootstrap_admin.py --email "$ADMIN_EMAIL"
fi

echo "==> Done. Database reset complete."
