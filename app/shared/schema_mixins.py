"""Reusable Pydantic validation mixins for input schemas."""

from pydantic import BaseModel, field_validator


class StripRequiredTextMixin(BaseModel):
    """Trims and rejects blank/whitespace-only values for common text fields.

    Applied to *input* schemas (Create/Update) only — never to read schemas, so
    existing rows still serialise. `check_fields=False` lets it decorate whichever
    of these fields the concrete schema actually declares. A `None` value (an
    omitted field on a partial update) passes through untouched.
    """

    @field_validator("name", "first_name", "last_name", check_fields=False)
    @classmethod
    def _strip_required_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("Este campo es requerido")
        return stripped
