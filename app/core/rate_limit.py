"""Application rate limiter (slowapi).

Keyed by client IP. The storage is in-memory by default, which means counters
live per worker process — acceptable for the current single-container deploy and
swappable for Redis (set rate_limit_storage_uri) without touching call sites.

Endpoints opt in with the `@limiter.limit(...)` decorator and must declare a
`request: Request` parameter so slowapi can resolve the client key.
"""

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_storage_uri,
    enabled=settings.rate_limit_enabled,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Return 429 with a Retry-After header (seconds until the window resets).

    slowapi's default handler omits Retry-After; clients (and our frontend) rely
    on it to back off, so we set it from the tripped limit's window length.
    """
    try:
        retry_after = exc.limit.limit.get_expiry()
    except Exception:
        retry_after = 60
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Intentá nuevamente más tarde."},
        headers={"Retry-After": str(retry_after)},
    )

# Named limits — centralized so the policy is visible in one place and reused
# across routers. Tune here, not at each call site.
LOGIN_LIMIT = "10/minute"
REFRESH_LIMIT = "30/minute"
REGISTER_LIMIT = "5/hour"
RESEND_LIMIT = "5/hour"
CHANGE_PASSWORD_LIMIT = "10/minute"
# Reset request triggers an email — keep tight to bound abuse / mail cost.
FORGOT_PASSWORD_LIMIT = "5/hour"
RESET_PASSWORD_LIMIT = "10/hour"
# Expensive (Gemini call) — keep tight to bound cost abuse.
CV_PREFILL_LIMIT = "20/hour"
UPLOAD_LIMIT = "60/hour"
