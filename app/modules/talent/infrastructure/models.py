from sqlalchemy import ForeignKey, Identity
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base
from app.shared.mixins import AuditMixin, SoftDeleteMixin


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, deferrable=True, initially="IMMEDIATE")


class TalentPool(Base, AuditMixin, SoftDeleteMixin):
    """talent.talent_pool — curated candidates surfaced for future opportunities.

    A candidate can be added multiple times from different source vacancies; the
    schema does not enforce uniqueness on candidate_id.
    """

    __tablename__ = "talent_pool"
    __table_args__ = {"schema": "talent"}

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    candidate_id: Mapped[int] = mapped_column(_fk("recruitment.candidates.id"))
    source_vacancy_id: Mapped[int | None] = mapped_column(
        _fk("recruitment.vacancies.id"), default=None
    )
