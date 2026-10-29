from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.campaigns.schemas import CampaignCreate, CampaignView, ContextIntakeView, MessageCreate
from app.campaigns.service import CampaignNotFoundError, CampaignService
from app.campaigns.state_machine import InvalidTransitionError

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


def service_from(request: Request) -> CampaignService:
    return request.app.state.campaign_service


@router.post("", response_model=CampaignView, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CampaignView:
    campaign = service_from(request).create(body.title, idempotency_key)
    return CampaignView(id=campaign.id, title=campaign.title, state=campaign.state)


@router.get("/{campaign_id}", response_model=CampaignView)
async def get_campaign(campaign_id: UUID, request: Request) -> CampaignView:
    try:
        campaign = service_from(request).get(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    return CampaignView(id=campaign.id, title=campaign.title, state=campaign.state)


@router.post("/{campaign_id}/messages", response_model=ContextIntakeView)
async def submit_message(
    campaign_id: UUID,
    body: MessageCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ContextIntakeView:
    try:
        result = await service_from(request).submit_message(
            campaign_id, body.content, idempotency_key
        )
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    except InvalidTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ContextIntakeView.model_validate(result)

