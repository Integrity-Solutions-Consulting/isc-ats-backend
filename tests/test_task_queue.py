"""Foundation tests for the background task queue (R0).

Covers the registry, the inline adapter (runs tasks in-process), the Arq adapter
(enqueues to a pool) and the backend factory. No Redis required.
"""

import pytest

from app.core.task_queue import (
    ArqTaskQueue,
    InlineTaskQueue,
    build_task_queue,
    get_task,
    register_task,
)


async def test_registry_resolves_and_raises_for_unknown() -> None:
    async def _noop() -> None:
        return None

    register_task("r0_noop", _noop)
    assert get_task("r0_noop") is _noop
    with pytest.raises(KeyError):
        get_task("r0_unknown_task")


async def test_inline_queue_runs_the_registered_task() -> None:
    calls: list[int] = []

    async def _record(value: int) -> None:
        calls.append(value)

    register_task("r0_record", _record)
    queue = InlineTaskQueue()
    await queue.enqueue("r0_record", 42)
    await queue.drain()

    assert calls == [42]


async def test_inline_queue_swallows_task_failure() -> None:
    async def _boom() -> None:
        raise RuntimeError("task failed")

    register_task("r0_boom", _boom)
    queue = InlineTaskQueue()
    await queue.enqueue("r0_boom")  # must not raise
    await queue.drain()


async def test_factory_returns_inline_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.settings.queue_backend", "inline")
    assert isinstance(build_task_queue(), InlineTaskQueue)


async def test_factory_arq_backend_requires_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.settings.queue_backend", "arq")
    with pytest.raises(RuntimeError):
        build_task_queue(None)


async def test_arq_queue_enqueues_to_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.settings.queue_backend", "arq")

    class _FakePool:
        def __init__(self) -> None:
            self.jobs: list[tuple[str, tuple]] = []

        async def enqueue_job(self, name: str, *args: object) -> None:
            self.jobs.append((name, args))

    pool = _FakePool()
    queue = build_task_queue(pool)
    assert isinstance(queue, ArqTaskQueue)

    await queue.enqueue("analyze_application", 7)
    assert pool.jobs == [("analyze_application", (7,))]
