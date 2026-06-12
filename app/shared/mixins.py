from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

# IPv6 max textual length — matches schema.sql varchar(45) on all ip_* columns.
IP_LENGTH = 45


class AuditMixin:
    """Audit columns present on all 29 tables of the schema.

    Mirrors: created_at, created_by, ip_created, updated_at, updated_by, ip_updated.
    Timestamps are timestamptz (DateTime(timezone=True)) to match schema.sql.
    `created_by` / `updated_by` reference auth.users.id at the DB level but are kept
    as plain integers here (the schema does not declare FKs on audit columns).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(default=None)
    ip_created: Mapped[str | None] = mapped_column(String(IP_LENGTH), default=None)

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        default=None,
    )
    updated_by: Mapped[int | None] = mapped_column(default=None)
    ip_updated: Mapped[str | None] = mapped_column(String(IP_LENGTH), default=None)


class SoftDeleteMixin:
    """Logical delete flag. `is_active = false` means logically deleted.

    There are no hard deletes in this system — repositories filter on is_active.
    """

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
