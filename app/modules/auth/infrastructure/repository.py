from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import RefreshToken, User
from app.shared.repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        # Email lookup is case-insensitive and whitespace-tolerant: mobile keyboards
        # and browser autofill routinely capitalize the first letter or append a
        # trailing space, which would otherwise miss a lowercase-stored address and
        # surface as a wrong-credentials 401 on the correct password.
        normalized = email.strip().lower()
        stmt = (
            select(User)
            .where(func.lower(User.email) == normalized)
            .where(User.is_active.is_(True))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, RefreshToken)

    async def get_valid_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Active, non-revoked, non-expired token matching the given hash."""
        now = datetime.now(UTC)
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .where(RefreshToken.is_active.is_(True))
            .where(RefreshToken.revoked_at.is_(None))
            .where(RefreshToken.expires_at > now)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(UTC)
        await self.session.flush()

    async def revoke_all_by_user_id(self, user_id: int) -> None:
        """Revoke every active, non-expired refresh token for the given user."""
        now = datetime.now(UTC)
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.is_active.is_(True))
            .where(RefreshToken.revoked_at.is_(None))
            .where(RefreshToken.expires_at > now)
        )
        tokens = list((await self.session.execute(stmt)).scalars().all())
        for t in tokens:
            t.revoked_at = now
        await self.session.flush()
