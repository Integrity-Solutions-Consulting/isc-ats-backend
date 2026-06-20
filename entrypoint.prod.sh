#!/bin/sh
set -e

# If a command is passed (e.g. the worker: "arq app.worker.WorkerSettings"),
# run it directly. Only the API entrypoint (no args) owns DB migrations.
if [ "$#" -gt 0 ]; then
  echo "==> Starting: $*"
  exec "$@"
fi

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
