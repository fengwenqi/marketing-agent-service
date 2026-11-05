from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.campaigns.state_machine import InvalidTransitionError
from app.database import get_session
from app.strategies.repository import StrategyRepository
from app.strategies.schemas import (
    StrategyConfirmRequest,
    StrategyFrameworkView,
    StrategyReviseRequest,
)
from app.strategies.service import (
    ExternalStrategyServiceError,
    InvalidStrategyOutputError,
    StaleStrategyVersionError,
    StrategyIdempotencyConflictError,
    StrategyNotFoundError,
    StrategyService,
)

router = APIRouter(
    prefix="/api/v1/campaigns/{campaign_id}/strategy-framework",
    tags=["marketing-strategy"],
)


def service_from(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StrategyService:
    return StrategyService(StrategyRepository(session), request.app.state.hermes)


ServiceDependency = Annotated[StrategyService, Depends(service_from)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def translate_strategy_error(error: Exception) -> HTTPException:
    if isinstance(error, StrategyNotFoundError):
        return HTTPException(status_code=404, detail="strategy framework not found")
    if isinstance(
        error,
        (InvalidTransitionError, StaleStrategyVersionError, StrategyIdempotencyConflictError),
    ):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, InvalidStrategyOutputError):
        return HTTPException(status_code=502, detail=str(error))
    if isinstance(error, ExternalStrategyServiceError):
        return HTTPException(status_code=503, detail=str(error))
    raise error


@router.post("/generate", response_model=StrategyFrameworkView)
async def generate_strategy(
    campaign_id: UUID,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StrategyFrameworkView:
    try:
        snapshot = await service.generate(campaign_id, idempotency_key)
    except Exception as error:
        raise translate_strategy_error(error) from error
    return StrategyFrameworkView.from_snapshot(snapshot)


@router.get("", response_model=StrategyFrameworkView)
async def get_strategy(
    campaign_id: UUID, service: ServiceDependency
) -> StrategyFrameworkView:
    try:
        snapshot = await service.get_for_campaign(campaign_id)
    except Exception as error:
        raise translate_strategy_error(error) from error
    return StrategyFrameworkView.from_snapshot(snapshot)


@router.post("/revise", response_model=StrategyFrameworkView)
async def revise_strategy(
    campaign_id: UUID,
    body: StrategyReviseRequest,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StrategyFrameworkView:
    try:
        snapshot = await service.revise(
            campaign_id,
            expected_version=body.expected_version,
            instruction=body.instruction,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise translate_strategy_error(error) from error
    return StrategyFrameworkView.from_snapshot(snapshot)


@router.post("/confirm", response_model=StrategyFrameworkView)
async def confirm_strategy(
    campaign_id: UUID,
    body: StrategyConfirmRequest,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StrategyFrameworkView:
    try:
        snapshot = await service.confirm(
            campaign_id,
            expected_version=body.expected_version,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise translate_strategy_error(error) from error
    return StrategyFrameworkView.from_snapshot(snapshot)
