from fastapi import APIRouter

from app.modules.comms.api import email_logs_routes, notifications_routes

router = APIRouter(prefix="/comms")
router.include_router(notifications_routes.router)
router.include_router(email_logs_routes.router)
