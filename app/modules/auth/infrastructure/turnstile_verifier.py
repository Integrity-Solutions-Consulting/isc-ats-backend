import httpx

from app.modules.auth.application.turnstile import TurnstileOutcome


class CloudflareTurnstileVerifier:
    """Turnstile adapter: POSTs the token to Cloudflare's siteverify endpoint.

    Distinguishes a real verdict (SUCCESS/FAILED) from an inability to reach one
    (UNAVAILABLE) so the caller can apply the right fail policy. A missing token
    is FAILED, not UNAVAILABLE — the client simply didn't solve the challenge.
    """

    def __init__(
        self,
        secret_key: str,
        verify_url: str,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._secret = secret_key
        self._url = verify_url
        self._timeout = timeout
        # Injectable transport keeps the adapter testable without real network.
        self._transport = transport

    async def verify(
        self, token: str | None, remote_ip: str | None
    ) -> TurnstileOutcome:
        if not token:
            return TurnstileOutcome.FAILED

        payload = {"secret": self._secret, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(self._url, data=payload)
        except httpx.HTTPError:
            # Timeout, connection error, DNS failure — no verdict reached.
            return TurnstileOutcome.UNAVAILABLE

        if response.status_code >= 500:
            return TurnstileOutcome.UNAVAILABLE

        try:
            data = response.json()
        except ValueError:
            return TurnstileOutcome.UNAVAILABLE

        return (
            TurnstileOutcome.SUCCESS
            if data.get("success") is True
            else TurnstileOutcome.FAILED
        )
