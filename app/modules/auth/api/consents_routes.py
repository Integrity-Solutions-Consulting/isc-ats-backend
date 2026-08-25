from fastapi import APIRouter, HTTPException, Request, status

from app.core.client_ip import get_client_ip
from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.consents_schemas import (
    MarketingConsentResponse,
    MarketingConsentUpdateRequest,
)
from app.modules.auth.application.consents_service import (
    CURRENT_POLICY_VERSION,
    ConsentsService,
)
from app.modules.auth.infrastructure.models import CONSENT_MARKETING

router = APIRouter(tags=["consents"])


def _require_candidate(current_user: CurrentUserDep) -> None:
    if current_user.portal != "candidate":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo los usuarios del portal de candidatos pueden gestionar este consentimiento",
        )


@router.get("/me/consents/marketing", response_model=MarketingConsentResponse)
async def get_marketing_consent(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> MarketingConsentResponse:
    _require_candidate(current_user)
    service = ConsentsService(session)
    decided = await service.has_ever_decided(current_user.user_id, CONSENT_MARKETING)
    subscribed = await service.is_currently_active(current_user.user_id, CONSENT_MARKETING)
    return MarketingConsentResponse(decided=decided, subscribed=subscribed)


@router.put("/me/consents/marketing", response_model=MarketingConsentResponse)
async def update_marketing_consent(
    data: MarketingConsentUpdateRequest,
    current_user: CurrentUserDep,
    session: SessionDep,
    request: Request,
) -> MarketingConsentResponse:
    _require_candidate(current_user)
    service = ConsentsService(session)
    ip = get_client_ip(request)

    if data.subscribed:
        await service.grant(
            current_user.user_id,
            CONSENT_MARKETING,
            policy_version=CURRENT_POLICY_VERSION,
            source=data.source,
            ip_address=ip,
        )
    else:
        currently_active = await service.is_currently_active(
            current_user.user_id, CONSENT_MARKETING
        )
        if currently_active:
            await service.revoke(current_user.user_id, CONSENT_MARKETING, source=data.source)
        else:
            # No active row yet — this is a first-ever decision ("No, gracias" from
            # a virgin user), not a toggle-off. Record it as a refusal so
            # has_ever_decided flips to True. Never reached from account_close or
            # any other path — this is the only legitimate way a refusal row gets
            # created outside registration (which never records refusals, D3).
            await service.record_refusal(
                current_user.user_id,
                CONSENT_MARKETING,
                policy_version=CURRENT_POLICY_VERSION,
                source=data.source,
                ip_address=ip,
            )

    decided = await service.has_ever_decided(current_user.user_id, CONSENT_MARKETING)
    subscribed = await service.is_currently_active(current_user.user_id, CONSENT_MARKETING)
    return MarketingConsentResponse(decided=decided, subscribed=subscribed)
