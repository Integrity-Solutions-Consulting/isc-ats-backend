"""CLI to reset an existing user's password by email.

`bootstrap_admin` is idempotent and deliberately does NOT overwrite an existing
user's password (ensure_admin_user returns the existing row untouched). Use this
when you know the admin email but lost the password and need to regain access:

    py -m uv run python scripts/reset_admin_password.py --email you@isc.com

The password is prompted securely if not passed with --password. It is validated
against the same policy the API enforces, then hashed and stored. The user's
must_change_password flag is cleared so the next login is clean.
"""

import argparse
import asyncio
import getpass

from sqlalchemy import select

import app.models_registry  # noqa: F401 — registers every model so FKs resolve
from app.core.database import async_session_factory
from app.core.security import hash_password
from app.modules.auth.infrastructure.models import User
from app.shared.validators import password_policy_error


async def _run(email: str, password: str) -> int:
    async with async_session_factory() as session:
        try:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is None:
                raise SystemExit(f"No user found with email {email!r}")
            user.password_hash = hash_password(password)
            user.must_change_password = False
            await session.commit()
            return user.id
        except Exception:
            await session.rollback()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset an existing user's password by email."
    )
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument(
        "--password",
        help="New password (prompted securely if omitted)",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("New password: ")
    policy_error = password_policy_error(password)
    if policy_error:
        parser.error(policy_error)

    user_id = asyncio.run(_run(args.email, password))
    print(f"Password reset for user id {user_id} <{args.email}>")


if __name__ == "__main__":
    main()
