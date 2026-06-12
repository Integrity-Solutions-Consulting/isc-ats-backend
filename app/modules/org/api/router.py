from fastapi import APIRouter

from app.modules.org.api import (
    client_companies_routes,
    contacts_routes,
    departments_routes,
    parameters_routes,
    process_stages_routes,
    processes_routes,
    profile_template_items_routes,
    profile_templates_routes,
)

# Aggregates every resource router inside the org bounded context.
router = APIRouter(prefix="/org")
router.include_router(parameters_routes.router)
router.include_router(departments_routes.router)
router.include_router(client_companies_routes.router)
router.include_router(contacts_routes.router)
router.include_router(processes_routes.router)
router.include_router(process_stages_routes.router)
router.include_router(profile_templates_routes.router)
router.include_router(profile_template_items_routes.router)
