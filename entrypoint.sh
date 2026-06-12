#!/bin/sh
set -e

echo "==> Syncing Python dependencies..."
uv sync

echo "==> Running Alembic migrations..."
uv run alembic upgrade head

echo "==> Starting uvicorn (hot-reload enabled)..."
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
