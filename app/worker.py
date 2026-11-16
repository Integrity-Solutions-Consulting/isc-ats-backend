"""Arq worker entrypoint — run with: ``arq app.worker.WorkerSettings``.

Tasks register themselves by name in ``app.core.task_queue._REGISTRY`` (the single
source of the task catalog). This module turns each registered coroutine into an
Arq job function (adding the ``ctx`` parameter Arq passes) with a retry policy, so
transient failures (Gemini rate limits, SMTP hiccups) are retried instead of lost.

Kept in lockstep with the inline queue: both execute the exact same registered
callables, so dev/test behavior matches production.
"""

from __future__ import annotations

import logging
from typing import Any

from arq import func as arq_func
from arq.connections import RedisSettings

# Importing the API router loads every route module, each of which registers its
# own background tasks in the queue registry at import time. This populates
# `_REGISTRY` before we build the Arq function list below — the worker and the
# in-process inline queue therefore share the exact same task catalog.
import app.api_router  # noqa: F401
from app.core.config import settings
from app.core.structured_logging import configure_app_logging
from app.core.task_queue import _REGISTRY, TaskFunc

logger = logging.getLogger(__name__)

# Transient failures (rate limits, SMTP timeouts) are retried with backoff.
_DEFAULT_MAX_TRIES = 4


def _as_arq_function(name: str, fn: TaskFunc) -> Any:
    """Adapt a `(*args)` task coroutine into an Arq `(ctx, *args)` job function."""

    async def _runner(ctx: dict[str, Any], *args: Any) -> None:
        logger.info("Trabajo iniciado", extra={"action": name})
        try:
            await fn(*args)
        except Exception:
            logger.exception("Trabajo fallido", extra={"action": name})
            raise
        else:
            logger.info("Trabajo finalizado", extra={"action": name})

    _runner.__name__ = name
    return arq_func(_runner, name=name, max_tries=_DEFAULT_MAX_TRIES)


def build_arq_functions() -> list[Any]:
    """Build the Arq function list from every registered task."""
    return [_as_arq_function(name, fn) for name, fn in _REGISTRY.items()]


async def worker_startup(ctx: dict[str, Any]) -> None:
    configure_app_logging()
    logger.info("Worker iniciado")


class WorkerSettings:
    """Arq worker configuration."""

    functions = build_arq_functions()
    on_startup = worker_startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Durable AI jobs manage their own delayed retries in SQL. Let the wrapper
    # finish its 240-second timeout and persist the outcome before Arq intervenes.
    job_timeout = 270
