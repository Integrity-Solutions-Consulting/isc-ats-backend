import io
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.dependencies import CurrentUser, CurrentUserDep, SessionDep
from app.modules.ai.application.analysis_service import analyze_application
from app.modules.ai.application.word_generator_service import generate_profile_word
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage
from app.modules.comms.application.email_templates import render_stage_change_email
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.applications_schemas import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from app.modules.recruitment.application.applications_service import (
    ApplicationNotFoundError,
    ApplicationReferenceError,
    ApplicationService,
    DuplicateApplicationError,
)
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationDocument,
)
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_repository import (
    CandidateRepository,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.minio_client import upload_file_to_minio
from app.modules.storage.infrastructure.models import File
from app.shared.ownership import is_candidate_portal, require_owner
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/applications", tags=["recruitment · applications"])


async def _require_candidate_owner(
    session: AsyncSession, current_user: CurrentUser, candidate_id: int
) -> None:
    """403 when a candidate-portal token targets another candidate's rows.

    Resolves the owning user of `candidate_id`; unknown candidates fall through
    (the service raises its own 404/422 afterwards) unless the caller is a
    candidate, in which case unknown ownership is rejected.
    """
    if not is_candidate_portal(current_user):
        return
    candidate = await BaseRepository(session, Candidate).get(candidate_id)
    require_owner(current_user, candidate.user_id if candidate else None)


def get_service(session: SessionDep) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[ApplicationService, Depends(get_service)]


async def _send_stage_change_email(application_id: int, new_stage_id: int) -> None:
    """Background task: notify the candidate that their application changed stage.

    Opens its own DB session (the request session is already closed) and resolves
    the candidate email plus the vacancy and stage names from their parameter
    catalogs. Never propagates — a failed notification must not affect the move.
    """
    async with async_session_factory() as session:
        try:
            application = await BaseRepository(session, Application).get(application_id)
            if application is None:
                return
            candidate = await BaseRepository(session, Candidate).get(
                application.candidate_id
            )
            if candidate is None:
                return
            user = await BaseRepository(session, User).get(candidate.user_id)
            vacancy = await BaseRepository(session, Vacancy).get(application.vacancy_id)
            stage = await BaseRepository(session, ProcessStage).get(new_stage_id)
            if user is None or vacancy is None or stage is None:
                return
            params = ParameterRepository(session)
            vacancy_name = await params.get(vacancy.vacancy_name_id)
            stage_name = await params.get(stage.stage_id)
            if vacancy_name is None or stage_name is None:
                return
            rendered = render_stage_change_email(
                candidate.first_name, vacancy_name.name, stage_name.name
            )
            dispatch = EmailDispatchService(session, build_email_sender())
            await dispatch.send(
                EmailMessage(
                    to_email=user.email,
                    subject=rendered.subject,
                    html_body=rendered.html_body,
                    text_body=rendered.text_body,
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()


@router.get(
    "",
    response_model=Page[ApplicationRead],
    dependencies=[Depends(require_permission("recruitment.applications.read"))],
)
async def list_applications(
    service: ServiceDep,
    session: SessionDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    vacancy_id: Annotated[int | None, Query()] = None,
    candidate_id: Annotated[int | None, Query()] = None,
    status_id: Annotated[int | None, Query()] = None,
) -> Page[ApplicationRead]:
    params = PageParams(page=page, size=size)
    # Candidates only ever see their own applications, whatever filter they ask for.
    if is_candidate_portal(current_user):
        own = await CandidateRepository(session).get_by_user_id(current_user.user_id)
        if own is None:
            return Page.create([], 0, params)
        candidate_id = own.id
    items, total = await service.list(
        params, vacancy_id=vacancy_id, candidate_id=candidate_id, status_id=status_id
    )
    return Page.create([ApplicationRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{application_id}",
    response_model=ApplicationRead,
    dependencies=[Depends(require_permission("recruitment.applications.read"))],
)
async def get_application(
    application_id: int,
    service: ServiceDep,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ApplicationRead:
    try:
        application = await service.get(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _require_candidate_owner(session, current_user, application.candidate_id)
    return ApplicationRead.model_validate(application)


@router.post(
    "",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.applications.create"))],
)
async def create_application(
    data: ApplicationCreate,
    service: ServiceDep,
    session: SessionDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> ApplicationRead:
    await _require_candidate_owner(session, current_user, data.candidate_id)
    try:
        created = await service.create(data, current_user)
    except ApplicationReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except DuplicateApplicationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    background_tasks.add_task(analyze_application, created.id)
    return ApplicationRead.model_validate(created)


@router.patch(
    "/{application_id}",
    response_model=ApplicationRead,
    dependencies=[Depends(require_permission("recruitment.applications.update"))],
)
async def update_application(
    application_id: int,
    data: ApplicationUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> ApplicationRead:
    try:
        existing = await service.get(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    old_stage_id = existing.current_stage_id
    try:
        updated = await service.update(application_id, data, current_user)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ApplicationReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    # Notify the candidate only when the Kanban stage actually changed.
    if updated.current_stage_id is not None and updated.current_stage_id != old_stage_id:
        background_tasks.add_task(
            _send_stage_change_email, updated.id, updated.current_stage_id
        )
    return ApplicationRead.model_validate(updated)


@router.get(
    "/{application_id}/generate-profile",
    dependencies=[Depends(require_permission("recruitment.applications.read"))],
)
async def generate_profile(
    application_id: int,
    service: ServiceDep,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    try:
        application = await service.get(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _require_candidate_owner(session, current_user, application.candidate_id)
    try:
        doc_bytes = await generate_profile_word(application_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    # ── Register the generated document in storage + application_documents ────
    try:
        file_name = f"perfil_{application_id}.docx"
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        stored_key = upload_file_to_minio(doc_bytes, file_name, mime)

        file_record = File(
            original_name=file_name,
            stored_key=stored_key,
            bucket="candidates-cvs",
            mime_type=mime,
            size_bytes=len(doc_bytes),
            is_public=False,
            entity_type="application_word",
            entity_id=application_id,
            created_by=current_user.user_id,
            ip_created=current_user.ip,
        )
        session.add(file_record)
        await session.flush()

        # Resolve the "generated" status parameter
        status_param = await ParameterRepository(session).get_by_type_and_code(
            "doc_generation_status", "generated"
        )
        if status_param is not None:
            doc_record = ApplicationDocument(
                application_id=application_id,
                file_id=file_record.id,
                status_id=status_param.id,
                created_by=current_user.user_id,
                ip_created=current_user.ip,
            )
            session.add(doc_record)
            await session.commit()
    except Exception:
        # Storage registration is best-effort — do not fail the download
        await session.rollback()

    return StreamingResponse(
        io.BytesIO(doc_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="perfil_{application_id}.docx"'},
    )


@router.post(
    "/{application_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("recruitment.applications.update"))],
)
async def trigger_analysis(
    application_id: int,
    service: ServiceDep,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        await service.get(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    background_tasks.add_task(analyze_application, application_id)
    return {"status": "queued"}


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.applications.delete"))],
)
async def delete_application(application_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
