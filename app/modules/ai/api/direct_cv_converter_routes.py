import base64
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import File as FastAPIFile

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.ai.application.direct_cv_converter_service import convert_cv
from app.modules.auth.api.authorization import require_permission
from app.modules.storage.application.upload_validation import (
    UploadTooLargeError,
    UploadTypeError,
    max_bytes_for,
    validate_upload_bytes,
)

router = APIRouter(prefix="/direct-cv-converter", tags=["ai · direct cv converter"])


@router.post(
    "",
    dependencies=[Depends(require_permission("recruitment.applications.read"))],
)
async def convert_direct_cv(
    session: SessionDep,
    _current_user: CurrentUserDep,
    file: Annotated[UploadFile, FastAPIFile(...)],
) -> dict:
    """Convert one PDF CV without creating a candidate or application record."""
    pdf_bytes = await file.read(max_bytes_for("cv") + 1)
    try:
        validate_upload_bytes("cv", pdf_bytes)
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except UploadTypeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    try:
        preview, document = await convert_cv(pdf_bytes, session)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "No se pudo convertir la hoja de vida",
        ) from exc

    return {
        "fileName": "perfil_convertido.docx",
        "preview": preview,
        "documentBase64": base64.b64encode(document).decode("ascii"),
    }
