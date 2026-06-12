from fastapi import APIRouter

from app.modules.talent.api import talent_pool_routes

router = APIRouter(prefix="/talent")
router.include_router(talent_pool_routes.router)
