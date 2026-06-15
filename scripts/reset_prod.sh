#!/bin/sh
# Reset the database from scratch and reseed system parameters.
#
# Wipes EVERYTHING (drops all schemas via `alembic downgrade base`), rebuilds the
# schema and re-runs all seed migrations (`alembic upgrade head`), then bootstraps
# the RBAC baseline + admin user.
#
# DESTRUCTIVE — deletes all data. Use only on environments with throwaway/test
# data. Never run against a database with real users you need to keep. Take a
# backup first if in doubt:  pg_dump "<sync DSN>" > backup.sql
#
# Usage (inside the backend service shell):
#     sh scripts/reset_prod.sh --email admin@integritysolutions.com.ec
#     sh scripts/reset_prod.sh --email admin@isc.com --yes   # skip confirmation
#
# Do NOT run scripts/seed_dev_data.py afterwards in production — that seeds fake
# demo data (banks, candidates). Business catalogs are loaded by HR via the UI.
set -e

# Prefer uv (dev image); fall back to plain commands (prod image).
if command -v uv >/dev/null 2>&1; then
  ALEMBIC="uv run alembic"
  PY="uv run python"
else
  ALEMBIC="alembic"
  PY="python"
fi

ADMIN_EMAIL=""
ASSUME_YES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --email) ADMIN_EMAIL="$2"; shift 2 ;;
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

echo "==> [1/3] Dropping all schemas (alembic downgrade base)..."
$ALEMBIC downgrade base

echo "==> [2/3] Rebuilding schema + seeding system parameters (alembic upgrade head)..."
$ALEMBIC upgrade head

echo "==> [3/3] Bootstrapping RBAC + admin user ($ADMIN_EMAIL)..."
$PY scripts/bootstrap_admin.py --email "$ADMIN_EMAIL"

echo "==> Done. Database reset complete."
