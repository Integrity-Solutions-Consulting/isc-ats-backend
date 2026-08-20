"""GET /auth/subscribers and /auth/subscribers/export — marketing-consent Slice 4.

Both endpoints call the exact same repository method,
ConsentsRepository.list_active_marketing_subscribers(), so the displayed count and
the exported file can never disagree (design D7).
"""

import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from app.core.dependencies import SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.application.consents_service import ConsentsService

router = APIRouter(
    prefix="/subscribers",
    tags=["subscribers"],
    dependencies=[Depends(require_permission("auth.subscribers.read"))],
)

_HEADER = ["Nombres", "Apellidos", "Correo", "Fecha de aceptación"]


def _naive(value: datetime) -> datetime:
    """Strip tzinfo — openpyxl raises on timezone-aware datetimes in a cell."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


@router.get("")
async def count_subscribers(session: SessionDep) -> dict[str, int]:
    rows = await ConsentsService(session).list_active_marketing_subscribers()
    return {"count": len(rows)}


@router.get("/export")
async def export_subscribers(session: SessionDep) -> StreamingResponse:
    rows = await ConsentsService(session).list_active_marketing_subscribers()

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADER)
    for row in rows:
        sheet.append(
            [row.first_name, row.last_name, row.email, _naive(row.accepted_at)]
        )

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="suscriptores.xlsx"'},
    )
