from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.campaigns.repository import CampaignRepository
from app.campaigns.schemas import (
    CampaignCreate,
    CampaignView,
    ContextIntakeView,
    MessageCreate,
    MessageView,
)
from app.campaigns.service import (
    CampaignNotFoundError,
    CampaignService,
    ExternalServiceError,
    HermesExecutionTimeoutError,
    HermesIdempotencyConflictError,
    IdempotencyConflictError,
    InvalidHermesOutputError,
    PreviousHermesRequestFailedError,
)
from app.campaigns.state_machine import InvalidTransitionError
from app.database import get_session

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


def service_from(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CampaignService:
    return CampaignService(CampaignRepository(session), request.app.state.hermes)


ServiceDependency = Annotated[CampaignService, Depends(service_from)]


@router.post("", response_model=CampaignView, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate,
    service: ServiceDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CampaignView:
    campaign = await service.create(body.title, idempotency_key)
    return CampaignView.model_validate(campaign)


@router.get("/{campaign_id}", response_model=CampaignView)
async def get_campaign(campaign_id: UUID, service: ServiceDependency) -> CampaignView:
    try:
        campaign = await service.get(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    return CampaignView.model_validate(campaign)


@router.get("/{campaign_id}/messages", response_model=list[MessageView])
async def list_messages(campaign_id: UUID, service: ServiceDependency) -> list[MessageView]:
    try:
        messages = await service.list_messages(campaign_id)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    # 页面历史以 PostgreSQL 为准；Hermes state.db 只负责 Agent 自身上下文。
    return [MessageView.model_validate(message) for message in messages]


@router.post("/{campaign_id}/messages", response_model=ContextIntakeView)
async def submit_message(
    campaign_id: UUID,
    body: MessageCreate,
    service: ServiceDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ContextIntakeView:
    try:
        result = await service.submit_message(campaign_id, body.content, idempotency_key)
    except CampaignNotFoundError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
    except InvalidTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidHermesOutputError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except PreviousHermesRequestFailedError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HERMES_REQUEST_ALREADY_FAILED",
                "message": str(error),
                "next_action": "RESEND_MESSAGE_WITH_NEW_REQUEST_ID",
            },
        ) from error
    except HermesIdempotencyConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HERMES_IDEMPOTENCY_CONFLICT",
                "message": str(error),
                "next_action": "RESEND_MESSAGE_WITH_NEW_REQUEST_ID",
            },
        ) from error
    except HermesExecutionTimeoutError as error:
        raise HTTPException(
            status_code=504,
            detail={
                "code": "HERMES_EXECUTION_TIMEOUT",
                "message": str(error),
                "next_action": "RESEND_MESSAGE_WITH_NEW_REQUEST_ID",
            },
        ) from error
    except ExternalServiceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ContextIntakeView.model_validate(result)
