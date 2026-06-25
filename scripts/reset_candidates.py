"""DANGER — hard-delete every candidate user and all data hanging off them.

Wipes candidates so a clean registration→verification→onboarding flow can be
re-validated from scratch. This is a HARD delete (real DELETE statements), the
deliberate exception to the system's soft-delete convention: these rows are
test data, and freeing the emails lets testers re-register the same addresses.

Scope: every auth.users row whose portal code is `candidate`. Staff users
(the admin) are never touched. Deletion runs children→parents in one
transaction; any failure rolls the whole thing back.

Usage (inside the deployed container):

    python scripts/reset_candidates.py            # DRY RUN — counts only, deletes nothing
    python scripts/reset_candidates.py --confirm  # actually deletes

The dependency order below is derived from the FK graph:

    users(candidate)
    ├── user_roles, refresh_tokens, notifications
    └── candidates
        ├── cv_parse_jobs, talent_pool
        └── applications
            ├── application_documents, application_notes, interviews
"""

import argparse
import asyncio

from sqlalchemy import text

import app.models_registry  # noqa: F401 — registers every model so FKs resolve
from app.core.database import async_session_factory

# Resolve the id sets once; every DELETE is scoped to these.
SQL_USER_IDS = text(
    """
    SELECT u.id
    FROM auth.users u
    JOIN org.parameters p ON p.id = u.portal_id
    WHERE p.type = 'user_portal' AND p.code = 'candidate'
    ORDER BY u.id
    """
)
SQL_CANDIDATE_IDS = text(
    "SELECT id FROM recruitment.candidates WHERE user_id = ANY(:uids)"
)
SQL_APPLICATION_IDS = text(
    "SELECT id FROM recruitment.applications WHERE candidate_id = ANY(:cids)"
)

# (label, DELETE sql, param-name). Order matters: children before parents.
DELETE_PLAN = [
    ("application_documents", "DELETE FROM recruitment.application_documents WHERE application_id = ANY(:aids)", "aids"),
    ("application_notes", "DELETE FROM recruitment.application_notes WHERE application_id = ANY(:aids)", "aids"),
    ("interviews", "DELETE FROM recruitment.interviews WHERE application_id = ANY(:aids)", "aids"),
    ("cv_parse_jobs", "DELETE FROM ai.cv_parse_jobs WHERE candidate_id = ANY(:cids)", "cids"),
    ("talent_pool", "DELETE FROM talent.talent_pool WHERE candidate_id = ANY(:cids)", "cids"),
    ("applications", "DELETE FROM recruitment.applications WHERE candidate_id = ANY(:cids)", "cids"),
    ("candidates", "DELETE FROM recruitment.candidates WHERE user_id = ANY(:uids)", "uids"),
    ("notifications", "DELETE FROM comms.notifications WHERE recipient_id = ANY(:uids)", "uids"),
    ("refresh_tokens", "DELETE FROM auth.refresh_tokens WHERE user_id = ANY(:uids)", "uids"),
    ("user_roles", "DELETE FROM auth.user_roles WHERE user_id = ANY(:uids)", "uids"),
    ("users", "DELETE FROM auth.users WHERE id = ANY(:uids)", "uids"),
]

# Same scoping as DELETE_PLAN, used to count rows in dry-run mode.
COUNT_PLAN = [(label, sql.replace("DELETE FROM", "SELECT count(*) FROM"), key) for label, sql, key in DELETE_PLAN]


async def _resolve_ids(session) -> dict[str, list[int]]:
    uids = list((await session.execute(SQL_USER_IDS)).scalars())
    cids = list((await session.execute(SQL_CANDIDATE_IDS, {"uids": uids})).scalars())
    aids = list((await session.execute(SQL_APPLICATION_IDS, {"cids": cids})).scalars())
    return {"uids": uids, "cids": cids, "aids": aids}


async def _run(confirm: bool) -> None:
    async with async_session_factory() as session:
        ids = await _resolve_ids(session)
        uids, cids, aids = ids["uids"], ids["cids"], ids["aids"]

        print(f"Candidate users : {len(uids)}  -> {uids}")
        print(f"Candidate rows  : {len(cids)}")
        print(f"Applications    : {len(aids)}")
        print("-" * 60)

        if not uids:
            print("No candidate users found. Nothing to do.")
            return

        if not confirm:
            print("DRY RUN — rows that WOULD be deleted (nothing deleted yet):\n")
            total = 0
            for label, sql, key in COUNT_PLAN:
                n = (await session.execute(text(sql), {key: ids[key]})).scalar() or 0
                total += n
                print(f"  {label:<22} {n:>6}")
            print(f"\n  {'TOTAL rows':<22} {total:>6}")
            print("\nRe-run with --confirm to delete.")
            return

        print("DELETING (hard) — children first, single transaction:\n")
        try:
            for label, sql, key in DELETE_PLAN:
                res = await session.execute(text(sql), {key: ids[key]})
                print(f"  deleted {res.rowcount:>6} from {label}")
            await session.commit()
            print("\nDone. Committed. Candidates wiped — testers can re-register from scratch.")
        except Exception:
            await session.rollback()
            print("\nERROR — rolled back. Nothing was deleted.")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete. Without this flag the script only counts (dry run).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.confirm))


if __name__ == "__main__":
    main()
