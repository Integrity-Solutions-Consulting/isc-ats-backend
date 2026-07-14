from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.dependencies import SessionDep
from app.modules.auth.api.assignments_schemas import ParameterTypesBody
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.application.bootstrap_service import grant_parameter_types_to_role
from app.modules.auth.infrastructure.models import Role, RoleParameterTypeGrant

router = APIRouter(prefix="/roles", tags=["auth · role parameter types"])


async def _assert_role_exists(session: SessionDep, role_id: int) -> None:
    stmt = select(Role.id).where(Role.id == role_id).where(Role.is_active.is_(True))
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Role {role_id} not found")


async def _active_parameter_types_for_role(session: SessionDep, role_id: int) -> list[str]:
    stmt = (
        select(RoleParameterTypeGrant.parameter_type)
        .where(RoleParameterTypeGrant.role_id == role_id)
        .where(RoleParameterTypeGrant.is_active.is_(True))
        .order_by(RoleParameterTypeGrant.parameter_type)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get(
    "/{role_id}/parameter-types",
    response_model=ParameterTypesBody,
    dependencies=[Depends(require_permission("auth.roles.read"))],
)
async def list_role_parameter_types(role_id: int, session: SessionDep) -> ParameterTypesBody:
    await _assert_role_exists(session, role_id)
    return ParameterTypesBody(parameter_types=await _active_parameter_types_for_role(session, role_id))


@router.put(
    "/{role_id}/parameter-types",
    response_model=ParameterTypesBody,
    dependencies=[Depends(require_permission("auth.roles.update"))],
)
async def replace_role_parameter_types(
    role_id: int, data: ParameterTypesBody, session: SessionDep
) -> ParameterTypesBody:
    await _assert_role_exists(session, role_id)
    await grant_parameter_types_to_role(session, role_id, set(data.parameter_types))
    return ParameterTypesBody(parameter_types=await _active_parameter_types_for_role(session, role_id))
