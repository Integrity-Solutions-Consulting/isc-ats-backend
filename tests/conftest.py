from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Import the registry so the FULL model metadata is registered for every test,
# regardless of which models a given test module imports. Without this, cross-
# schema FKs (e.g. candidates.avatar_file_id -> storage.files) fail to resolve
# when a test only imports a subset of the models.
import app.models_registry  # noqa: F401
from app.core.config import settings


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
