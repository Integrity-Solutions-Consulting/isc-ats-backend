from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import IP_LENGTH, AuditMixin, SoftDeleteMixin


class User(Base, AuditMixin, SoftDeleteMixin):
    """auth.users — one user belongs to exactly one portal (staff | candidate).

    portal_id references org.parameters (type=user_portal). Code branches on the
    parameter's CODE, never on this numeric id.
    """

    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    portal_id: Mapped[int] = mapped_column(
        ForeignKey("org.parameters.id", deferrable=True, initially="IMMEDIATE")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    email_verified: Mapped[bool] = mapped_column(default=False)
    must_change_password: Mapped[bool] = mapped_column(default=False)


class RefreshToken(Base, AuditMixin, SoftDeleteMixin):
    """auth.refresh_tokens — server-side record of issued refresh tokens.

    Only the sha256 hash is stored. Rotation revokes the old row (revoked_at)
    and inserts a fresh one on each refresh.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("auth.users.id", deferrable=True, initially="IMMEDIATE")
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(IP_LENGTH), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Role(Base, AuditMixin, SoftDeleteMixin):
    """auth.roles — a named bundle of permissions (thin CRUD).

    Roles are linked to permissions through auth.role_permissions and to users
    through auth.user_roles.
    """

    __tablename__ = "roles"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, default=None)


class Permission(Base, AuditMixin, SoftDeleteMixin):
    """auth.permissions — a single authorizable action, identified by `code`.

    `code` is the stable identifier checked at authorization time (e.g.
    "org.departments.create"); `module` groups permissions for the UI.
    """

    __tablename__ = "permissions"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    module: Mapped[str | None] = mapped_column(String(50), default=None)


class RolePermission(Base, AuditMixin, SoftDeleteMixin):
    """auth.role_permissions — junction granting a permission to a role.

    Composite primary key (role_id, permission_id); no surrogate id.
    """

    __tablename__ = "role_permissions"
    __table_args__ = {"schema": "auth"}

    role_id: Mapped[int] = mapped_column(
        ForeignKey("auth.roles.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("auth.permissions.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )


class RoleParameterTypeGrant(Base, AuditMixin, SoftDeleteMixin):
    """auth.role_parameter_type_grants — junction granting a role write access to
    an org.parameters catalog type (org.parameters.type).

    Composite primary key (role_id, parameter_type); no surrogate id. Unlike
    RolePermission, `parameter_type` is a plain string, not a foreign key —
    org.parameters.type is not backed by a modeled entity anywhere in the schema
    (see Parameter.type in app.modules.org.infrastructure.models), so there is
    nothing to reference.
    """

    __tablename__ = "role_parameter_type_grants"
    __table_args__ = {"schema": "auth"}

    role_id: Mapped[int] = mapped_column(
        ForeignKey("auth.roles.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    parameter_type: Mapped[str] = mapped_column(String(50), primary_key=True)


class UserRole(Base, AuditMixin, SoftDeleteMixin):
    """auth.user_roles — junction assigning a role to a user.

    Composite primary key (user_id, role_id); no surrogate id.
    """

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "auth"}

    user_id: Mapped[int] = mapped_column(
        ForeignKey("auth.users.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("auth.roles.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )


class MenuItem(Base, AuditMixin, SoftDeleteMixin):
    """auth.menu_items — a navigation entry for a portal, optionally gated.

    Self-referential tree via `parent_id`. `portal_id` points at org.parameters
    (type=user_portal). When `permission_id` is set, the item is shown only to
    users holding that permission. `order` sorts siblings.
    """

    __tablename__ = "menu_items"
    __table_args__ = {"schema": "auth"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth.menu_items.id", deferrable=True, initially="IMMEDIATE"),
        default=None,
    )
    portal_id: Mapped[int] = mapped_column(
        ForeignKey("org.parameters.id", deferrable=True, initially="IMMEDIATE")
    )
    label: Mapped[str] = mapped_column(String(100))
    route: Mapped[str | None] = mapped_column(String(200), default=None)
    icon: Mapped[str | None] = mapped_column(String(50), default=None)
    permission_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth.permissions.id", deferrable=True, initially="IMMEDIATE"),
        default=None,
    )
    # "order" is a SQL reserved word — map the column name explicitly.
    order: Mapped[int] = mapped_column("order")
