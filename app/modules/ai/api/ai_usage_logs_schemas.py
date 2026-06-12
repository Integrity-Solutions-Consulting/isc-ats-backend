from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AiUsageLogCreate(BaseModel):
    action: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None


class AiUsageLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    is_active: bool
    created_at: datetime
    created_by: int | None
