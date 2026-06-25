"""CLI to list users with their portal, active flag and verification state.

Runs inside the deployed container where the web terminal mangles long pastes,
so this lives in the image and is invoked with a short command:

    python scripts/list_users.py

`is_active = false` means the row is logically deleted (no hard deletes exist).
The portal code (staff | candidate) comes from org.parameters via portal_id.
"""

import asyncio

from sqlalchemy import select

import app.models_registry  # noqa: F401 — registers every model so FKs resolve
from app.core.database import async_session_factory
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter


async def _run() -> None:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(
                    User.id,
                    User.email,
                    User.email_verified,
                    User.is_active,
                    User.must_change_password,
                    User.last_login_at,
                    Parameter.code.label("portal"),
                )
                .join(Parameter, Parameter.id == User.portal_id)
                .order_by(User.id)
            )
        ).all()

    header = (
        f"{'id':>3}  {'portal':<10} {'verif':<5} {'active':<6} "
        f"{'chgpw':<5} {'last_login':<16} email"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        last = r.last_login_at.strftime("%Y-%m-%d %H:%M") if r.last_login_at else "-"
        print(
            f"{r.id:>3}  {r.portal:<10} "
            f"{('yes' if r.email_verified else 'no'):<5} "
            f"{('yes' if r.is_active else 'NO'):<6} "
            f"{('yes' if r.must_change_password else 'no'):<5} "
            f"{last:<16} {r.email}"
        )
    print(f"\nTotal: {len(rows)} users")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
