from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.campaigns.domain import StageCode
from app.database import get_session
from app.gates.policy import GateNotAvailableError
from app.media_plans.repository import MediaPlanRepository
from app.media_plans.schemas import (
    MediaPlanConfirmRequest,
    MediaPlanGenerateRequest,
    MediaPlanReviseRequest,
    StageMediaPlanView,
)
from app.media_plans.service import (
    ExternalMediaPlanServiceError,
    InvalidMediaPlanOutputError,
    MediaPlanIdempotencyConflictError,
    MediaPlanNotConfirmableError,
    MediaPlanNotFoundError,
    MediaPlanService,
    StageNotOpenError,
    StaleMediaPlanVersionError,
    StaleStrategySourceError,
)

router = APIRouter(
    prefix="/api/v1/campaigns/{campaign_id}/media-plans/{stage_code}",
    tags=["media-plans"],
)


def service_from(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaPlanService:
    return MediaPlanService(MediaPlanRepository(session), request.app.state.hermes)


ServiceDependency = Annotated[MediaPlanService, Depends(service_from)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def raise_http_error(error: Exception) -> NoReturn:
    if isinstance(error, MediaPlanNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={"code": "MEDIA_PLAN_NOT_FOUND", "message": str(error)},
        ) from error
    if isinstance(error, StageNotOpenError):
        raise HTTPException(
            status_code=409,
            detail={"code": "STAGE_NOT_OPEN", "message": str(error)},
        ) from error
    if isinstance(error, StaleStrategySourceError):
        raise HTTPException(
            status_code=409,
            detail={"code": "STALE_STRATEGY_SOURCE", "message": str(error)},
        ) from error
    if isinstance(error, StaleMediaPlanVersionError):
        raise HTTPException(
            status_code=409,
            detail={"code": "STALE_MEDIA_PLAN_VERSION", "message": str(error)},
        ) from error
    if isinstance(error, MediaPlanIdempotencyConflictError):
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(error)},
        ) from error
    if isinstance(error, GateNotAvailableError):
        raise HTTPException(
            status_code=409,
            detail={"code": "GATE_NOT_AVAILABLE", "message": str(error)},
        ) from error
    if isinstance(error, MediaPlanNotConfirmableError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLAN_NOT_CONFIRMABLE",
                "message": str(error),
                "missing_fields": error.missing_fields,
                "validation_errors": error.validation_errors,
            },
        ) from error
    if isinstance(error, InvalidMediaPlanOutputError):
        raise HTTPException(
            status_code=502,
            detail={"code": "INVALID_MEDIA_PLAN_OUTPUT", "message": str(error)},
        ) from error
    if isinstance(error, ExternalMediaPlanServiceError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEDIA_PLAN_SERVICE_UNAVAILABLE",
                "message": str(error),
            },
        ) from error
    raise error


@router.post("/generate", response_model=StageMediaPlanView)
async def generate_media_plan(
    campaign_id: UUID,
    stage_code: StageCode,
    body: MediaPlanGenerateRequest,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StageMediaPlanView:
    try:
        snapshot = await service.generate(
            campaign_id,
            stage_code,
            confirmation_inputs=body.confirmation_inputs,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise_http_error(error)
    return StageMediaPlanView.from_snapshot(snapshot)


@router.get("", response_model=StageMediaPlanView)
async def get_media_plan(
    campaign_id: UUID,
    stage_code: StageCode,
    service: ServiceDependency,
) -> StageMediaPlanView:
    try:
        snapshot = await service.get_for_stage(campaign_id, stage_code)
    except Exception as error:
        raise_http_error(error)
    return StageMediaPlanView.from_snapshot(snapshot)


@router.post("/revise", response_model=StageMediaPlanView)
async def revise_media_plan(
    campaign_id: UUID,
    stage_code: StageCode,
    body: MediaPlanReviseRequest,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StageMediaPlanView:
    try:
        snapshot = await service.revise(
            campaign_id,
            stage_code,
            expected_version=body.expected_version,
            instruction=body.instruction,
            confirmation_inputs=body.confirmation_inputs,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise_http_error(error)
    return StageMediaPlanView.from_snapshot(snapshot)


@router.post("/confirm", response_model=StageMediaPlanView)
async def confirm_media_plan(
    campaign_id: UUID,
    stage_code: StageCode,
    body: MediaPlanConfirmRequest,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> StageMediaPlanView:
    try:
        snapshot = await service.confirm(
            campaign_id,
            stage_code,
            expected_version=body.expected_version,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise_http_error(error)
    return StageMediaPlanView.from_snapshot(snapshot)
