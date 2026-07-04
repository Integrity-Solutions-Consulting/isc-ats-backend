from sqlalchemy import select

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.recruitment.api.application_notes_schemas import (
    ApplicationNoteCreate,
    ApplicationNoteRead,
    ApplicationNoteUpdate,
    _author_name_from_email,
)
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationNote,
)
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class ApplicationNoteNotFoundError(Exception):
    pass


class ApplicationNoteReferenceError(Exception):
    """The referenced application does not exist."""


class ApplicationNoteService:
    """Thin CRUD for recruitment.application_notes (validates the application FK)."""

    def __init__(
        self,
        repository: BaseRepository[ApplicationNote],
        applications: BaseRepository[Application],
        users: BaseRepository[User] | None = None,
    ) -> None:
        self.repository = repository
        self.applications = applications
        self.users = users

    async def _enrich_author(self, note: ApplicationNote) -> ApplicationNoteRead:
        """Build an ApplicationNoteRead with a resolved author_name."""
        author_name = "Staff"
        if note.created_by is not None and self.users is not None:
            user = await self.users.get(note.created_by)
            if user is not None:
                author_name = _author_name_from_email(user.email)
        read = ApplicationNoteRead.model_validate(note)
        read.author_name = author_name
        return read

    async def enrich_authors(
        self, notes: list[ApplicationNote]
    ) -> list[ApplicationNoteRead]:
        """Enrich a batch of notes with author names using ONE user query.

        Avoids the N+1 that per-note _enrich_author would incur: collect the
        distinct author ids and resolve them all at once.
        """
        author_ids = {
            note.created_by for note in notes if note.created_by is not None
        }
        emails_by_id: dict[int, str] = {}
        if author_ids and self.users is not None:
            stmt = select(User).where(User.id.in_(author_ids))
            users = (await self.users.session.execute(stmt)).scalars().all()
            emails_by_id = {user.id: user.email for user in users}

        reads: list[ApplicationNoteRead] = []
        for note in notes:
            read = ApplicationNoteRead.model_validate(note)
            email = emails_by_id.get(note.created_by) if note.created_by else None
            read.author_name = (
                _author_name_from_email(email) if email is not None else "Staff"
            )
            reads.append(read)
        return reads

    async def list(
        self, params: PageParams, *, application_id: int | None = None
    ) -> tuple[list[ApplicationNote], int]:
        filters = {"application_id": application_id} if application_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, note_id: int) -> ApplicationNote:
        note = await self.repository.get(note_id)
        if note is None:
            raise ApplicationNoteNotFoundError(f"Note {note_id} not found")
        return note

    async def create(
        self, data: ApplicationNoteCreate, actor: CurrentUser
    ) -> ApplicationNote:
        if await self.applications.get(data.application_id) is None:
            raise ApplicationNoteReferenceError(
                f"application_id={data.application_id} not found"
            )
        note = ApplicationNote(
            application_id=data.application_id,
            content=data.content,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(note)

    async def update(
        self, note_id: int, data: ApplicationNoteUpdate, actor: CurrentUser
    ) -> ApplicationNote:
        note = await self.get(note_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(note, changes)

    async def delete(self, note_id: int) -> None:
        note = await self.get(note_id)
        await self.repository.soft_delete(note)
