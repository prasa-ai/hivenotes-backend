"""
Sessions router — manages lifecycle of webapp sessions.

Endpoints
─────────
  POST  /sessions/start                — create a new session, persist to Azure Table Storage
  POST  /sessions/{session_id}/end     — mark a session as ended
  GET   /sessions/{session_id}         — retrieve session status

Azure Table Storage schema
──────────────────────────
  Table  : <AZURE_SESSIONS_TABLE_NAME>  (default: "UserSessions")
  PartitionKey : user_id
  RowKey       : session_id
  Properties   : device_id, app_version, platform, os_version,
                 ip_address, user_agent, started_at, ended_at,
                 status, metadata (JSON string)
"""
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from azure.data.tables.aio import TableServiceClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError

from app.config import settings
from app.models.session import (
    StartSessionRequest,
    StartSessionResponse,
    EndSessionRequest,
    SessionStatusResponse,
)
from app.dependencies import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/sessions/start",
    response_model=StartSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new app session",
)
async def start_session(
    payload: StartSessionRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """
    Generate a new session_id, persist session metadata to Azure Table Storage,
    and return the session_id for the frontend app to include in subsequent
    requests as the `X-Session-Id` header.
    """
    session_id = str(uuid4())
    started_at = _utcnow_iso()

    entity = {
        "PartitionKey": user_id,
        "RowKey": session_id,
        "user_id": user_id,
        "device_id": payload.device_id or "",
        "app_version": payload.app_version or "",
        "platform": payload.platform or "",
        "os_version": payload.os_version or "",
        "ip_address": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
        "started_at": started_at,
        "ended_at": "",
        "status": "active",
        "metadata": json.dumps(payload.metadata),
    }

    logger.info("start_session: creating session %s for user %s", session_id, user_id)

    try:
        async with TableServiceClient.from_connection_string(
            settings.azure_sessions_table_connection_string
        ) as service:
            table = service.get_table_client(settings.azure_sessions_table_name)
            try:
                await table.create_table()
            except Exception:
                pass  # table already exists
            await table.create_entity(entity=entity)

    except HttpResponseError as exc:
        logger.error("start_session: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to persist session. Please try again.",
        )

    return StartSessionResponse(
        session_id=session_id,
        user_id=user_id,
        started_at=started_at,
    )


@router.post(
    "/sessions/{session_id}/end",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End an active session",
)
async def end_session(
    session_id: str,
    payload: EndSessionRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Mark a session as ended in Azure Table Storage.
    Uses PartitionKey=user_id so only the owning user can end their own session.
    """
    ended_at = _utcnow_iso()

    try:
        async with TableServiceClient.from_connection_string(
            settings.azure_sessions_table_connection_string
        ) as service:
            table = service.get_table_client(settings.azure_sessions_table_name)
            await table.update_entity(
                mode="merge",
                entity={
                    "PartitionKey": user_id,
                    "RowKey": session_id,
                    "status": "ended",
                    "ended_at": ended_at,
                    **({"metadata": json.dumps({"end_reason": payload.reason})} if payload.reason else {}),
                },
            )

    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found for this user.",
        )
    except HttpResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to end session: {exc.message}",
        )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionStatusResponse,
    summary="Get session status",
)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Retrieve session metadata. Only the owning user can query their own session.
    """
    try:
        async with TableServiceClient.from_connection_string(
            settings.azure_sessions_table_connection_string
        ) as service:
            table = service.get_table_client(settings.azure_sessions_table_name)
            entity = await table.get_entity(partition_key=user_id, row_key=session_id)

    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    except HttpResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Table Storage error: {exc.message}",
        )

    return SessionStatusResponse(
        session_id=session_id,
        user_id=entity.get("user_id", ""),
        status=entity.get("status", "unknown"),
        started_at=entity.get("started_at", ""),
        ended_at=entity.get("ended_at") or None,
        platform=entity.get("platform") or None,
        app_version=entity.get("app_version") or None,
        metadata=json.loads(entity.get("metadata", "{}")),
    )
