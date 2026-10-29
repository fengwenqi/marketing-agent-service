from uuid import UUID

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class CampaignView(BaseModel):
    id: UUID
    title: str
    state: str


class ContextIntakeView(BaseModel):
    campaign_id: UUID
    campaign_state: str
    result: dict

