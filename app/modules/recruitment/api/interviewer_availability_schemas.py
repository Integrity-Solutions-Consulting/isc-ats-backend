from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class AvailabilityBase(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: time
    end_time: time
    slot_duration_min: int = Field(default=60, ge=1)
    buffer_min: int = Field(
        default=0, ge=0, description="Dead time (minutes) added between consecutive slots"
    )


class AvailabilityCreate(AvailabilityBase):
    user_id: int


class AvailabilityUpdate(BaseModel):
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    slot_duration_min: int | None = Field(default=None, ge=1)
    buffer_min: int | None = Field(default=None, ge=0)


class AvailabilityRead(AvailabilityBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_active: bool
    created_at: datetime
