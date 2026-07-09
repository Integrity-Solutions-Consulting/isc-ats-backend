import httpx

from app.modules.auth.application.turnstile import TurnstileOutcome
from app.modules.auth.infrastructure.turnstile_verifier import (
    CloudflareTurnstileVerifier,
)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _verifier(handler) -> CloudflareTurnstileVerifier:
    return CloudflareTurnstileVerifier(
        secret_key="secret",
        verify_url=VERIFY_URL,
        transport=httpx.MockTransport(handler),
    )


async def test_missing_token_is_failed_without_calling_cloudflare() -> None:
    """An empty token is a FAILED verdict — the client never solved the challenge,
    so there's nothing to verify and no network call is made."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"success": True})

    outcome = await _verifier(handler).verify(None, "1.2.3.4")

    assert outcome is TurnstileOutcome.FAILED
    assert called is False


async def test_success_true_maps_to_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True})

    outcome = await _verifier(handler).verify("tok", "1.2.3.4")
    assert outcome is TurnstileOutcome.SUCCESS


async def test_success_false_maps_to_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error-codes": ["bad"]})

    outcome = await _verifier(handler).verify("tok", "1.2.3.4")
    assert outcome is TurnstileOutcome.FAILED


async def test_server_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    outcome = await _verifier(handler).verify("tok", "1.2.3.4")
    assert outcome is TurnstileOutcome.UNAVAILABLE


async def test_network_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    outcome = await _verifier(handler).verify("tok", "1.2.3.4")
    assert outcome is TurnstileOutcome.UNAVAILABLE


async def test_sends_secret_response_and_remoteip() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        for pair in body.split("&"):
            key, _, value = pair.partition("=")
            captured[key] = value
        return httpx.Response(200, json={"success": True})

    await _verifier(handler).verify("the-token", "9.9.9.9")

    assert captured["secret"] == "secret"
    assert captured["response"] == "the-token"
    assert captured["remoteip"] == "9.9.9.9"
