from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Import the registry so the FULL model metadata is registered for every test,
# regardless of which models a given test module imports. Without this, cross-
# schema FKs (e.g. candidates.avatar_file_id -> storage.files) fail to resolve
# when a test only imports a subset of the models.
import app.models_registry  # noqa: F401
from app.core.config import settings
from app.core.login_throttle import InMemoryLoginThrottle
from app.core.rate_limit import limiter
from app.core.task_queue import get_task
from app.core.token_denylist import InMemoryTokenDenylist
from app.main import app as _app


@pytest.fixture(autouse=True)
def _disable_rate_limit() -> Generator[None, None, None]:
    """Rate limiting is off by default in tests so unrelated cases aren't throttled.

    The dedicated rate-limit test re-enables it explicitly.
    """
    limiter.enabled = False
    yield
    limiter.enabled = False


class _AwaitingTaskQueue:
    """Test queue that runs enqueued tasks synchronously (awaited).

    Production/dev use the fire-and-forget InlineTaskQueue (or Arq). Tests await
    the task so assertions on side effects are deterministic — matching the old
    BackgroundTasks semantics that ASGITransport awaited.
    """

    async def enqueue(self, task_name: str, *args: object) -> None:
        await get_task(task_name)(*args)


@pytest.fixture(autouse=True)
def _synchronous_task_queue() -> Generator[None, None, None]:
    _app.state.task_queue = _AwaitingTaskQueue()
    yield


@pytest.fixture(autouse=True)
def _reset_token_denylist() -> Generator[None, None, None]:
    """Fresh denylist per test — DB ids are reused across rolled-back tests, so a
    leftover revocation marker would otherwise revoke a different test's token."""
    _app.state.token_denylist = InMemoryTokenDenylist()
    yield


@pytest.fixture(autouse=True)
def _reset_login_throttle() -> Generator[None, None, None]:
    """Fresh login throttle per test so a lock from one test can't leak into the
    next (rolled-back tests reuse emails/ids)."""
    _app.state.login_throttle = InMemoryLoginThrottle()
    yield


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """A rolled-back session on a per-test engine — integration tests persist nothing.

    A dedicated NullPool engine is built per test so asyncpg connections never
    span event loops (pytest-asyncio uses a fresh loop per test).
    Requires the local Postgres (isc_ats) up and migrated to head.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()
