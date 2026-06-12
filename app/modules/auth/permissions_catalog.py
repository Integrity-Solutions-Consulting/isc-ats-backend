"""Canonical catalog of authorization permissions — the single source of truth.

Routes guard with these codes (require_permission) and the bootstrap routine syncs
them into auth.permissions. Keeping the list here (not in a migration) avoids drift
between what the code checks and what the database knows.

Code convention: "{module}.{resource}.{action}".
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionSpec:
    code: str
    name: str
    module: str


_ACTION_VERB = {
    "read": "View",
    "create": "Create",
    "update": "Update",
    "delete": "Delete",
    "assign": "Assign",
    "revoke": "Revoke",
    "grant": "Grant",
}

_CRUD = ("read", "create", "update", "delete")


def _specs(
    module: str, resource: str, label: str, actions: tuple[str, ...]
) -> tuple[PermissionSpec, ...]:
    return tuple(
        PermissionSpec(
            code=f"{module}.{resource}.{action}",
            name=f"{_ACTION_VERB[action]} {label}",
            module=module,
        )
        for action in actions
    )


PERMISSION_CATALOG: tuple[PermissionSpec, ...] = (
    # org bounded context
    *_specs("org", "parameters", "parameters", _CRUD),
    *_specs("org", "departments", "departments", _CRUD),
    *_specs("org", "client_companies", "client companies", _CRUD),
    *_specs("org", "contacts", "contacts", _CRUD),
    *_specs("org", "processes", "processes", _CRUD),
    *_specs("org", "process_stages", "process stages", _CRUD),
    *_specs("org", "profile_templates", "profile templates", _CRUD),
    *_specs("org", "profile_template_items", "profile template items", _CRUD),
    # auth bounded context — RBAC administration
    *_specs("auth", "users", "users", ("read", "create", "update")),
    *_specs("auth", "roles", "roles", _CRUD),
    *_specs("auth", "permissions", "permissions", _CRUD),
    *_specs("auth", "user_roles", "user-role assignments", ("read", "assign", "revoke")),
    *_specs(
        "auth", "role_permissions", "role-permission grants", ("read", "grant", "revoke")
    ),
    *_specs("auth", "menu_items", "menu items", _CRUD),
    # recruitment bounded context
    *_specs("recruitment", "vacancies", "vacancies", _CRUD),
    *_specs("recruitment", "candidates", "candidates", _CRUD),
    *_specs("recruitment", "applications", "applications", _CRUD),
    *_specs("recruitment", "application_documents", "application documents", _CRUD),
    *_specs("recruitment", "application_notes", "application notes", _CRUD),
    *_specs("recruitment", "interviews", "interviews", _CRUD),
    *_specs(
        "recruitment", "interviewer_availability", "interviewer availability", _CRUD
    ),
    # talent bounded context
    *_specs("talent", "talent_pool", "talent pool", ("read", "create", "delete")),
    # comms bounded context
    *_specs("comms", "notifications", "notifications", _CRUD),
    *_specs("comms", "email_logs", "email logs", ("read", "create")),
    # storage bounded context
    *_specs("storage", "files", "files", _CRUD),
    # ai bounded context
    *_specs("ai", "cv_parse_jobs", "CV parse jobs", _CRUD),
    *_specs("ai", "vacancy_promo_images", "vacancy promo images", ("read", "create", "delete")),
    *_specs("ai", "ai_usage_logs", "AI usage logs", ("read", "create")),
)

ALL_CODES: frozenset[str] = frozenset(spec.code for spec in PERMISSION_CATALOG)
