from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class CampaignView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    state: str
    context: dict | None = None


class ContextIntakeView(BaseModel):
    campaign_id: UUID
    campaign_state: str
    result: dict


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    payload: dict | None
    sequence: int
    created_at: datetime

