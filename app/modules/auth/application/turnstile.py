from enum import Enum
from typing import Protocol


class TurnstileOutcome(Enum):
    """Result of verifying a Turnstile token against Cloudflare.

    SUCCESS and FAILED are verdicts Cloudflare returned. UNAVAILABLE means we
    could not reach a verdict at all (timeout, network error, 5xx, bad body) —
    the caller decides the fail policy: register fails closed, login fails open.
    """

    SUCCESS = "success"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class TurnstileVerifier(Protocol):
    """Port: validates a client-solved Turnstile token.

    Wired at the API composition root. Returns None from the factory when the
    feature is disabled, so AuthService skips the check entirely.
    """

    async def verify(
        self, token: str | None, remote_ip: str | None
    ) -> TurnstileOutcome: ...
