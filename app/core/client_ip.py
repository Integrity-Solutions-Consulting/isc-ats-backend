"""Real client IP resolution for rate limiting and audit.

The API sits behind the Next.js proxy on an internal network, so the peer
address (``request.client.host``) is always the proxy, never the user. That
makes per-IP rate limits shared across every user. When ``trust_proxy_headers``
is enabled, read the real client IP from the ``X-Real-Client-IP`` header that our
proxy sets — and always overwrites — so a client cannot spoof it. The header is
trusted only because the backend is unreachable directly (internal network);
keep it OFF wherever the backend is exposed.
"""

from __future__ import annotations

from fastapi import Request
from slowapi.util import get_remote_address

from app.core.config import settings

_CLIENT_IP_HEADER = "x-real-client-ip"


def get_client_ip(request: Request) -> str:
    """Return the real client IP, honouring the proxy header only when trusted."""
    if settings.trust_proxy_headers:
        forwarded = request.headers.get(_CLIENT_IP_HEADER)
        if forwarded:
            return forwarded.strip()
    return get_remote_address(request)
