from fastapi import APIRouter

from app.modules.ai.api import (
    ai_usage_logs_routes,
    cv_parse_jobs_routes,
    vacancy_promo_images_routes,
)

router = APIRouter(prefix="/ai")
router.include_router(cv_parse_jobs_routes.router)
router.include_router(vacancy_promo_images_routes.router)
router.include_router(ai_usage_logs_routes.router)
