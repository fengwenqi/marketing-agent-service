from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.campaigns.domain import StageCode
from app.database import get_session
from app.gates.policy import GateNotAvailableError
from app.video_scripts.repository import VideoScriptRepository
from app.video_scripts.schemas import (
    VideoScriptBatchConfirmRequest,
    VideoScriptBatchView,
    VideoScriptReviseRequest,
    VideoScriptView,
)
from app.video_scripts.service import (
    ExternalVideoScriptServiceError,
    InvalidVideoScriptOutputError,
    MediaPlanNotConfirmedError,
    NoRetryableScriptJobsError,
    ScriptBatchConfirmationConflictError,
    StageNotOpenError,
    StaleVideoScriptVersionError,
    VideoScriptAlreadyConfirmedError,
    VideoScriptBatchNotFoundError,
    VideoScriptBatchService,
    VideoScriptWorkflowService,
)

router = APIRouter(
    prefix="/api/v1/campaigns/{campaign_id}",
    tags=["video-scripts"],
)

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


def repository_from(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VideoScriptRepository:
    return VideoScriptRepository(session)


RepositoryDependency = Annotated[VideoScriptRepository, Depends(repository_from)]


def raise_http_error(error: Exception) -> NoReturn:
    if isinstance(error, VideoScriptBatchNotFoundError):
        raise HTTPException(
            404, detail={"code": "VIDEO_SCRIPT_NOT_FOUND", "message": str(error)}
        ) from error
    if isinstance(error, StageNotOpenError):
        raise HTTPException(
            409, detail={"code": "STAGE_NOT_OPEN", "message": str(error)}
        ) from error
    if isinstance(error, MediaPlanNotConfirmedError):
        raise HTTPException(
            409, detail={"code": "MEDIA_PLAN_NOT_CONFIRMED", "message": str(error)}
        ) from error
    if isinstance(error, NoRetryableScriptJobsError):
        raise HTTPException(
            409, detail={"code": "NO_RETRYABLE_SCRIPT_JOBS", "message": str(error)}
        ) from error
    if isinstance(error, StaleVideoScriptVersionError):
        raise HTTPException(
            409, detail={"code": "STALE_VIDEO_SCRIPT_VERSION", "message": str(error)}
        ) from error
    if isinstance(error, VideoScriptAlreadyConfirmedError):
        raise HTTPException(
            409, detail={"code": "VIDEO_SCRIPT_ALREADY_CONFIRMED", "message": str(error)}
        ) from error
    if isinstance(error, ScriptBatchConfirmationConflictError):
        raise HTTPException(
            409, detail={"code": "VIDEO_SCRIPT_CONFLICT", "message": str(error)}
        ) from error
    if isinstance(error, GateNotAvailableError):
        raise HTTPException(
            409, detail={"code": "GATE_NOT_AVAILABLE", "message": str(error)}
        ) from error
    if isinstance(error, InvalidVideoScriptOutputError):
        raise HTTPException(
            502, detail={"code": "INVALID_VIDEO_SCRIPT_OUTPUT", "message": str(error)}
        ) from error
    if isinstance(error, ExternalVideoScriptServiceError):
        raise HTTPException(
            503, detail={"code": "VIDEO_SCRIPT_SERVICE_UNAVAILABLE", "message": str(error)}
        ) from error
    raise error


@router.post(
    "/video-script-batches/{stage_code}",
    response_model=VideoScriptBatchView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_script_batch(
    campaign_id: UUID,
    stage_code: StageCode,
    request: Request,
    repository: RepositoryDependency,
    idempotency_key: IdempotencyKey,
) -> VideoScriptBatchView:
    try:
        snapshot = await VideoScriptBatchService(repository).create_or_get(
            campaign_id, stage_code, idempotency_key
        )
        if snapshot.batch.status in {"PENDING", "RUNNING"}:
            request.app.state.video_script_executor.schedule(snapshot.batch.id)
    except Exception as error:
        raise_http_error(error)
    return VideoScriptBatchView.from_snapshot(snapshot)


@router.get(
    "/video-script-batches/{stage_code}",
    response_model=VideoScriptBatchView,
)
async def get_video_script_batch(
    campaign_id: UUID,
    stage_code: StageCode,
    repository: RepositoryDependency,
) -> VideoScriptBatchView:
    try:
        snapshot = await VideoScriptBatchService(repository).get_batch(campaign_id, stage_code)
    except Exception as error:
        raise_http_error(error)
    return VideoScriptBatchView.from_snapshot(snapshot)


@router.post(
    "/video-script-batches/{stage_code}/retry",
    response_model=VideoScriptBatchView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_video_script_batch(
    campaign_id: UUID,
    stage_code: StageCode,
    request: Request,
    repository: RepositoryDependency,
    idempotency_key: IdempotencyKey,
) -> VideoScriptBatchView:
    try:
        snapshot = await VideoScriptBatchService(repository).retry_failed(
            campaign_id, stage_code, idempotency_key
        )
        request.app.state.video_script_executor.schedule(snapshot.batch.id)
    except Exception as error:
        raise_http_error(error)
    return VideoScriptBatchView.from_snapshot(snapshot)


@router.post(
    "/video-script-batches/{stage_code}/confirm",
    response_model=VideoScriptBatchView,
)
async def confirm_video_script_batch(
    campaign_id: UUID,
    stage_code: StageCode,
    body: VideoScriptBatchConfirmRequest,
    request: Request,
    repository: RepositoryDependency,
    idempotency_key: IdempotencyKey,
) -> VideoScriptBatchView:
    try:
        snapshot = await VideoScriptWorkflowService(
            repository, request.app.state.hermes
        ).confirm_batch(
            campaign_id,
            stage_code,
            expected_script_versions=[item.to_domain() for item in body.expected_script_versions],
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise_http_error(error)
    return VideoScriptBatchView.from_snapshot(snapshot)


@router.get(
    "/video-scripts/{stage_code}",
    response_model=VideoScriptBatchView,
)
async def list_video_scripts(
    campaign_id: UUID,
    stage_code: StageCode,
    repository: RepositoryDependency,
) -> VideoScriptBatchView:
    try:
        snapshot = await VideoScriptBatchService(repository).get_batch(campaign_id, stage_code)
    except Exception as error:
        raise_http_error(error)
    return VideoScriptBatchView.from_snapshot(snapshot)


@router.get(
    "/video-scripts/{stage_code}/{script_id}",
    response_model=VideoScriptView,
)
async def get_video_script(
    campaign_id: UUID,
    stage_code: StageCode,
    script_id: UUID,
    request: Request,
    repository: RepositoryDependency,
) -> VideoScriptView:
    if stage_code is not StageCode.A:
        raise_http_error(StageNotOpenError(stage_code.value))
    try:
        snapshot = await VideoScriptWorkflowService(
            repository, request.app.state.hermes
        ).get_script(campaign_id, script_id)
    except Exception as error:
        raise_http_error(error)
    return VideoScriptView.from_snapshot(snapshot)


@router.post(
    "/video-scripts/{stage_code}/{script_id}/revise",
    response_model=VideoScriptView,
)
async def revise_video_script(
    campaign_id: UUID,
    stage_code: StageCode,
    script_id: UUID,
    body: VideoScriptReviseRequest,
    request: Request,
    repository: RepositoryDependency,
    idempotency_key: IdempotencyKey,
) -> VideoScriptView:
    if stage_code is not StageCode.A:
        raise_http_error(StageNotOpenError(stage_code.value))
    try:
        snapshot = await VideoScriptWorkflowService(
            repository, request.app.state.hermes
        ).revise_script(
            campaign_id,
            script_id,
            expected_version=body.expected_version,
            instruction=body.instruction,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        raise_http_error(error)
    return VideoScriptView.from_snapshot(snapshot)
