from fastapi import APIRouter

from app.modules.auth.api import (
    auth_routes,
    menu_items_routes,
    permissions_routes,
    role_permissions_routes,
    roles_routes,
    user_roles_routes,
    users_routes,
)

# Aggregates every resource router inside the auth bounded context.
router = APIRouter(prefix="/auth")
router.include_router(auth_routes.router)
router.include_router(roles_routes.router)
router.include_router(role_permissions_routes.router)
router.include_router(permissions_routes.router)
router.include_router(user_roles_routes.router)
router.include_router(menu_items_routes.router)
router.include_router(users_routes.router)
