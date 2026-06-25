from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProcessStageBase(BaseModel):
    process_id: int = Field(examples=[1])
    stage_id: int = Field(examples=[1], description="org.parameters id of type 'stage'")
    order: int = Field(ge=1, examples=[1])
    is_final_positive: bool = False
    is_initial: bool = False


class ProcessStageCreate(ProcessStageBase):
    pass


class ProcessStageUpdate(BaseModel):
    stage_id: int | None = None
    order: int | None = Field(default=None, ge=1)
    is_final_positive: bool | None = None


class ProcessStageRead(ProcessStageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
