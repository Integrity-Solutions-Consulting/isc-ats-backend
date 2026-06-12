from fastapi import APIRouter

from app.modules.ai.api.router import router as ai_router
from app.modules.auth.api.router import router as auth_router
from app.modules.comms.api.router import router as comms_router
from app.modules.org.api.router import router as org_router
from app.modules.recruitment.api.router import router as recruitment_router
from app.modules.storage.api.router import router as storage_router
from app.modules.talent.api.router import router as talent_router

# Top-level API router. Each bounded context contributes its own sub-router.
api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(org_router)
api_router.include_router(recruitment_router)
api_router.include_router(talent_router)
api_router.include_router(comms_router)
api_router.include_router(storage_router)
api_router.include_router(ai_router)
