"""CLI to bootstrap the RBAC baseline and the first admin user.

Run after `alembic upgrade head` (needs the user_portal:staff parameter seeded):

    py -m uv run python scripts/bootstrap_admin.py --email you@isc.com

The password is prompted securely if not passed with --password. Safe to re-run:
permissions are re-synced and an existing admin user is left untouched.
"""

import argparse
import asyncio
import getpass

from app.core.database import async_session_factory
from app.modules.auth.application.bootstrap_service import (
    BootstrapError,
    BootstrapResult,
    bootstrap_admin,
)


async def _run(email: str, password: str) -> BootstrapResult:
    async with async_session_factory() as session:
        try:
            result = await bootstrap_admin(session, email, password)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap the RBAC baseline + an admin user."
    )
    parser.add_argument("--email", required=True, help="Admin email")
    parser.add_argument(
        "--password",
        help="Admin password (prompted securely if omitted)",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Admin password: ")
    if not password.strip():
        parser.error("password must not be empty")

    try:
        result = asyncio.run(_run(args.email, password))
    except BootstrapError as exc:
        parser.error(str(exc))

    print("RBAC bootstrap complete:")
    print(f"  permissions synced : {result.permissions_synced}")
    print(f"  admin role id      : {result.role_id} (grants: {result.grants})")
    status = "created" if result.user_created else "already existed"
    print(f"  admin user id      : {result.user_id} ({status}) <{args.email}>")


if __name__ == "__main__":
    main()
