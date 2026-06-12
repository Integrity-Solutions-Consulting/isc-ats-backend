from fastapi import APIRouter

from app.modules.storage.api import files_routes

router = APIRouter(prefix="/storage")
router.include_router(files_routes.router)
