from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.consents_repository import ConsentsRepository
from app.modules.auth.infrastructure.models import Consent

# Bump together with the frontend's LegalDocument.version (src/features/legal/
# content.ts) — see design D4. Both must agree on the current policy version.
CURRENT_POLICY_VERSION = "2.0"


class ConsentsService:
    """Thin wrapper over ConsentsRepository.

    Slice 1 establishes the service-layer seam; later slices (2-4) add the
    real orchestration logic (e.g. gating on has_ever_decided at
    registration/login, wiring the account-close cascade, the marketing
    export endpoint).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ConsentsRepository(session)

    async def has_ever_decided(self, user_id: int, consent_type: str) -> bool:
        return await self._repo.has_ever_decided(user_id, consent_type)

    async def is_currently_active(self, user_id: int, consent_type: str) -> bool:
        return await self._repo.is_currently_active(user_id, consent_type)

    async def grant(
        self,
        user_id: int,
        consent_type: str,
        *,
        source: str,
        policy_version: str = CURRENT_POLICY_VERSION,
        ip_address: str | None = None,
        accepted_at: datetime | None = None,
    ) -> Consent:
        return await self._repo.grant(
            user_id,
            consent_type,
            policy_version=policy_version,
            source=source,
            ip_address=ip_address,
            accepted_at=accepted_at,
        )

    async def record_refusal(
        self,
        user_id: int,
        consent_type: str,
        *,
        source: str,
        policy_version: str = CURRENT_POLICY_VERSION,
        ip_address: str | None = None,
    ) -> Consent:
        return await self._repo.record_refusal(
            user_id,
            consent_type,
            policy_version=policy_version,
            source=source,
            ip_address=ip_address,
        )

    async def revoke(
        self, user_id: int, consent_type: str, *, source: str | None = None
    ) -> bool:
        return await self._repo.revoke(user_id, consent_type, source=source)

    async def list_active_marketing_subscribers(self) -> Sequence[Row]:
        return await self._repo.list_active_marketing_subscribers()
