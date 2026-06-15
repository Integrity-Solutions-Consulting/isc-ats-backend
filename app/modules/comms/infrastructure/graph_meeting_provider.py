import time

import httpx

from app.modules.comms.application.meeting_provider import MeetingRequest, MeetingResult

_GRAPH = "https://graph.microsoft.com/v1.0"


class GraphMeetingProvider:
    """MeetingProvider backed by Microsoft Graph (Teams online meetings).

    Uses the OAuth2 client-credentials flow (app-only). Requires an Azure AD app
    with application permission OnlineMeetings.ReadWrite.All (admin-consented) and
    a Teams application access policy granting it rights to create meetings on
    behalf of the organizer. The organizer is addressed by their M365 UPN/email.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        # Reuse the cached token until ~1 minute before expiry.
        if self._token and now < self._token_expires_at - 60:
            return self._token
        resp = await client.post(
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = now + float(payload.get("expires_in", 3600))
        return self._token

    async def create_meeting(self, request: MeetingRequest) -> MeetingResult:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token = await self._get_token(client)
                # The meeting is created under the organizer's user object; the
                # join link is open so external (e.g. Gmail) candidates can join.
                resp = await client.post(
                    f"{_GRAPH}/users/{request.organizer_email}/onlineMeetings",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "subject": request.subject,
                        "startDateTime": request.start.isoformat(),
                        "endDateTime": request.end.isoformat(),
                    },
                )
            if resp.status_code >= 400:
                return MeetingResult(
                    success=False,
                    error_detail=f"Graph {resp.status_code}: {resp.text[:500]}",
                )
            data = resp.json()
            return MeetingResult(
                success=True,
                join_url=data.get("joinWebUrl"),
                meeting_id=data.get("id"),
            )
        except Exception as exc:  # noqa: BLE001 - any auth/HTTP error becomes a non-success result
            return MeetingResult(success=False, error_detail=str(exc)[:1000])
