from typing import Literal

from pydantic import BaseModel


class MarketingConsentResponse(BaseModel):
    """GET/PUT /auth/me/consents/marketing response.

    `decided` mirrors ConsentsService.has_ever_decided (any row exists —
    grant, refusal, or revoked); `subscribed` mirrors is_currently_active
    (a non-revoked row exists right now).
    """

    decided: bool
    subscribed: bool


class MarketingConsentUpdateRequest(BaseModel):
    """PUT /auth/me/consents/marketing request body.

    `source` records where the decision was made — the modal shown to a
    virgin candidate on /mi-perfil, or the toggle on the profile card for a
    candidate who already decided once.
    """

    subscribed: bool
    source: Literal["profile_modal", "profile_toggle"]
