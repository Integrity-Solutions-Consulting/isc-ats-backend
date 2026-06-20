"""Background task queue — port + adapters.

Two interchangeable backends, chosen by `settings.queue_backend`:

- ``InlineTaskQueue`` ("inline", default): schedules the coroutine on the running
  event loop (fire-and-forget, like the old FastAPI BackgroundTasks). No Redis,
  so local dev and the test suite need no extra infrastructure.
- ``ArqTaskQueue`` ("arq"): enqueues a job in Redis for a separate Arq worker to
  execute durably, with retries. Used in production.

Call sites depend only on the ``TaskQueue`` protocol and a task NAME, never on a
concrete backend — the same enqueue call works in dev and production.

Tasks register themselves by name in a single registry so both the inline queue
(which runs them) and the worker (which executes enqueued jobs) resolve the same
callables. The registry is populated by ``app.worker`` at import time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

logger = logging.getLogger(__name__)

TaskFunc = Callable[..., Awaitable[None]]

# name -> coroutine function. Populated by app.worker (the single source of the
# task catalog), so the inline queue and the Arq worker stay in lockstep.
_REGISTRY: dict[str, TaskFunc] = {}


def register_task(name: str, func: TaskFunc) -> None:
    """Register an enqueueable task under a stable name."""
    _REGISTRY[name] = func


def get_task(name: str) -> TaskFunc:
    """Resolve a registered task, or raise KeyError if the name is unknown."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown task '{name}' — is it registered in app.worker?") from exc


class TaskQueue(Protocol):
    """Enqueue a registered task by name with positional, JSON-serializable args."""

    async def enqueue(self, task_name: str, *args: Any) -> None: ...


class InlineTaskQueue:
    """Run tasks in-process on the event loop — no Redis required.

    Mirrors the previous fire-and-forget behavior: enqueue returns immediately and
    the task runs concurrently. Failures are logged, never propagated. Keeps strong
    references to pending tasks so they are not garbage-collected mid-flight.
    """

    def __init__(self) -> None:
        self._pending: set[asyncio.Task[None]] = set()

    async def enqueue(self, task_name: str, *args: Any) -> None:
        func = get_task(task_name)
        task = asyncio.create_task(self._run(task_name, func, *args))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _run(self, task_name: str, func: TaskFunc, *args: Any) -> None:
        try:
            await func(*args)
        except Exception:
            logger.exception("Inline task '%s' failed", task_name)

    async def drain(self) -> None:
        """Await all in-flight tasks. Test helper — not used in production."""
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)


class ArqTaskQueue:
    """Enqueue jobs into Redis via an Arq pool; the worker executes them."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def enqueue(self, task_name: str, *args: Any) -> None:
        await self._pool.enqueue_job(task_name, *args)


def build_task_queue(arq_pool: Any | None = None) -> TaskQueue:
    """Pick the queue backend from settings.

    "arq" requires a live Redis pool (created at app startup); everything else
    falls back to the in-process inline queue so dev/test need no Redis.
    """
    from app.core.config import settings

    if settings.queue_backend == "arq":
        if arq_pool is None:
            raise RuntimeError("queue_backend='arq' requires a Redis pool")
        return ArqTaskQueue(arq_pool)
    return InlineTaskQueue()


def get_task_queue(request: Request) -> TaskQueue:
    """FastAPI dependency: the app-wide TaskQueue set at startup."""
    return request.app.state.task_queue


TaskQueueDep = Annotated[TaskQueue, Depends(get_task_queue)]
