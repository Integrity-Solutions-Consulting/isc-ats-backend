from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class Notification(Base, AuditMixin, SoftDeleteMixin):
    """comms.notifications — in-app notifications sent to a user.

    `channel_id` is an org.parameters catalog entry (type: notification_channel).
    `related_entity_type` / `related_entity_id` are a polymorphic reference to
    any entity (vacancy, application, interview, etc.).
    `read_at` is null when unread; set to now() when the user marks it read.
    """

    __tablename__ = "notifications"
    __table_args__ = {"schema": "comms"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    recipient_id: Mapped[int] = mapped_column(_fk("auth.users.id"))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text, default=None)
    channel_id: Mapped[int | None] = mapped_column(_fk("org.parameters.id"), default=None)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), default=None)
    related_entity_id: Mapped[int | None] = mapped_column(default=None)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, default=None
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class EmailLog(Base, AuditMixin, SoftDeleteMixin):
    """comms.email_logs — immutable record of every outbound email attempt.

    Written by the system; no update semantics. `status_id` references
    org.parameters (type: email_status).
    """

    __tablename__ = "email_logs"
    __table_args__ = {"schema": "comms"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(300), default=None)
    status_id: Mapped[int] = mapped_column(_fk("org.parameters.id"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
