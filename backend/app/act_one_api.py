"""HTTP routes for the first act, independent of the application module."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.act_one import preview_act_one_pnl, read_act_one, start_act_one, upgrade_hiring
from app.act_one_schemas import (
    ActOneSnapshot,
    EmptyActOneCommand,
    PnlPreviewInputs,
    PnlPreviewResult,
)
from app.auth import require_admin, unauthorized

router = APIRouter()
logger = logging.getLogger(__name__)
participant_bearer = HTTPBearer(auto_error=False)


def participant_credential(
    credentials: HTTPAuthorizationCredentials | None = Depends(participant_bearer),
) -> str:
    """Require a participant Bearer before attempting any database access."""
    if credentials is None:
        raise unauthorized()
    return credentials.credentials


@router.post(
    "/sessions/{session_id}/acts/1/start",
    response_model=ActOneSnapshot,
    dependencies=[Depends(require_admin)],
)
async def start_first_act(
    request: Request,
    session_id: UUID,
    payload: EmptyActOneCommand | None = None,
) -> ActOneSnapshot:
    response = await run_in_threadpool(start_act_one, session_id)
    try:
        await request.app.state.lobby_hub.publish(session_id)
    except Exception:
        # Persistence has already committed. A retry may republish the snapshot.
        logger.warning("Lobby publication failed after act one start")
    return response


@router.get("/sessions/{session_id}/acts/1", response_model=ActOneSnapshot)
def get_first_act(
    session_id: UUID, credential: str = Depends(participant_credential)
) -> ActOneSnapshot:
    return read_act_one(session_id, credential)


@router.post(
    "/sessions/{session_id}/acts/1/hiring/upgrade", response_model=ActOneSnapshot
)
def upgrade_first_hiring_level(
    session_id: UUID,
    payload: EmptyActOneCommand | None = None,
    credential: str = Depends(participant_credential),
) -> ActOneSnapshot:
    return upgrade_hiring(session_id, credential)


@router.post(
    "/sessions/{session_id}/acts/1/pnl/preview", response_model=PnlPreviewResult
)
def preview_first_act_pnl(
    session_id: UUID,
    payload: PnlPreviewInputs,
    credential: str = Depends(participant_credential),
) -> PnlPreviewResult:
    return preview_act_one_pnl(session_id, credential, payload)
