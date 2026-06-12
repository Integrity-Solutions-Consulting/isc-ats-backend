from fastapi import APIRouter

from app.modules.recruitment.api import (
    application_documents_routes,
    application_notes_routes,
    applications_routes,
    candidates_routes,
    interviewer_availability_routes,
    interviews_routes,
    vacancies_routes,
)

# Aggregates every resource router inside the recruitment bounded context.
router = APIRouter(prefix="/recruitment")
router.include_router(vacancies_routes.router)
router.include_router(candidates_routes.router)
router.include_router(applications_routes.router)
router.include_router(application_documents_routes.router)
router.include_router(application_notes_routes.router)
router.include_router(interviews_routes.router)
router.include_router(interviewer_availability_routes.router)
