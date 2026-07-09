from app.core.config import Settings
from app.core.config import settings as global_settings
from app.modules.auth.application.turnstile import TurnstileVerifier
from app.modules.auth.infrastructure.turnstile_verifier import (
    CloudflareTurnstileVerifier,
)


def build_turnstile_verifier(
    config: Settings | None = None,
) -> TurnstileVerifier | None:
    """Return the Turnstile verifier, or None when the feature is disabled.

    None means AuthService skips the check entirely — so local dev and tests run
    without real Cloudflare keys, and the gate is a single env-var switch in prod.
    """
    config = config or global_settings
    if not config.turnstile_enabled:
        return None
    return CloudflareTurnstileVerifier(
        secret_key=config.turnstile_secret_key,
        verify_url=config.turnstile_verify_url,
    )
