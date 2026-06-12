from sqlalchemy import BigInteger, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


class File(Base, AuditMixin, SoftDeleteMixin):
    """storage.files — metadata for an object stored in a bucket.

    The binary lives in object storage (keyed by `stored_key`); this row is the
    queryable record. `entity_type`/`entity_id` loosely link a file to whatever
    owns it (e.g. candidate avatar/cv) without a hard FK.
    """

    __tablename__ = "files"
    __table_args__ = {"schema": "storage"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    original_name: Mapped[str] = mapped_column(String(500))
    stored_key: Mapped[str] = mapped_column(String(1000), unique=True)
    bucket: Mapped[str] = mapped_column(String(100))
    mime_type: Mapped[str | None] = mapped_column(String(100), default=None)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    is_public: Mapped[bool] = mapped_column(default=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), default=None)
    entity_id: Mapped[int | None] = mapped_column(Integer, default=None)
