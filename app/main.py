from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api_router import api_router
from app.core.config import settings
from app.core.login_throttle import InMemoryLoginThrottle, build_login_throttle
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.task_queue import InlineTaskQueue, build_task_queue
from app.core.token_denylist import InMemoryTokenDenylist, build_token_denylist
from app.modules.storage.infrastructure.minio_client import init_minio


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks before the application starts serving requests."""
    # Ensure the MinIO bucket exists — safe to call multiple times (idempotent).
    init_minio()

    # Background task queue + access-token denylist. Only the "arq" backend
    # touches Redis; "inline" (default) needs no connection, so dev/test start
    # without Redis and keep the denylist in-process.
    arq_pool = None
    denylist_redis = None
    if settings.queue_backend == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings
        from redis.asyncio import Redis

        arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        # Separate plain-Redis client: the denylist is read on every authenticated
        # request, independent of the arq job pool. The login throttle shares it.
        denylist_redis = Redis.from_url(settings.redis_url)
    app.state.task_queue = build_task_queue(arq_pool)
    app.state.token_denylist = build_token_denylist(denylist_redis)
    app.state.login_throttle = build_login_throttle(denylist_redis)

    try:
        yield
    finally:
        if arq_pool is not None:
            await arq_pool.close()
        if denylist_redis is not None:
            await denylist_redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        lifespan=lifespan,
    )

    # Rate limiting — slowapi reads app.state.limiter; the handler turns a
    # tripped limit into 429 with a Retry-After header.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # Safe defaults so app.state.task_queue / token_denylist always exist, even
    # when the lifespan does not run (ASGITransport in tests). The lifespan
    # upgrades these to the Redis-backed variants when queue_backend="arq".
    app.state.task_queue = InlineTaskQueue()
    app.state.token_denylist = InMemoryTokenDenylist()
    app.state.login_throttle = InMemoryLoginThrottle()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
