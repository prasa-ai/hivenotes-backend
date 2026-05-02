"""
Sessions router — conditional backend CRUD for therapy session records.

Storage backend selection
─────────────────────────
When ``settings.enable_cosmos_db`` is True, session CRUD uses Cosmos DB.
When False, the same endpoints transparently use Azure Table Storage.

Cosmos DB structure
───────────────────
Container: sessions
Partition key: /therapist_id
All queries are scoped to a therapist first.

Shared document fields
──────────────────────
    id                   : unique session id (UUID)
    therapist_id         : partition key / PartitionKey — used for scoping queries
    patient_id           : SHA-256 hash (no patient names stored)
    status               : session status (uploaded, processing, completed, etc.)
  filename             : str | None
  content_type         : str | None
  audio_blob_path      : str | None
  soap_blob_path       : str | None
  transcript_blob_path : str | None

Endpoints
─────────
  POST   /sessions                                     — create session (multipart audio upload)
  GET    /sessions?therapist_id=...                    — list all sessions for a therapist
  GET    /sessions/patient?therapist_id=&patient_first_name=&patient_last_name=  — list patient sessions
  PUT    /sessions/patient?therapist_id=&patient_first_name=&patient_last_name=  — update latest session
  GET    /sessions/{id}?therapist_id=&patient_first_name=&patient_last_name=   — get session
  PUT    /sessions/{id}?therapist_id=&patient_first_name=&patient_last_name=   — partial update
  DELETE /sessions/{id}?therapist_id=&patient_first_name=&patient_last_name=   — delete
  GET    /sessions/jobs/{job_id}                       — poll SOAP workflow job status
"""
import hashlib
import json
import logging
import uuid
import base64
from datetime import datetime, timezone
from typing import Any

from azure.cosmos import PartitionKey
from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import CosmosClient
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables.aio import TableServiceClient
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile, status

from app.config import settings
from app.models.session import SessionResponse, SessionUpdate, JobStatusResponse, SessionUploadResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Keep sessions isolated from therapist records in settings.azure_table_name.
SESSIONS_TABLE_NAME = f"{settings.azure_table_name}-sessions"

# ── In-memory SOAP job store ───────────────────────────────────────────────────
_job_store: dict[str, dict] = {}

ALLOWED_AUDIO_TYPES = {
    "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav",
    "audio/aac", "audio/ogg", "audio/webm", "audio/m4a", "audio/x-m4a",
}


# ── Patient-ID hashing (HIPAA: no PII stored in DB) ─────────────────────────

def _hash_patient_id(therapist_id: str, first_name: str, last_name: str) -> str:
    """Return SHA-256 hex digest of 'therapist_id:first_name_lower:last_name_lower'.
    Deterministic — same inputs always produce the same patient_id.
    """
    raw = f"{therapist_id}:{first_name.strip().lower()}:{last_name.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalize_optional(value: Any) -> Any:
    if value == "":
        return None
    return value


def _entity_to_session_response(entity: dict[str, Any]) -> SessionResponse:
    return SessionResponse(
        id=entity.get("id") or entity.get("session_id") or entity.get("RowKey", ""),
        therapist_id=entity["therapist_id"],
        patient_id=entity["patient_id"],
        status=_normalize_optional(entity.get("status")),
        filename=_normalize_optional(entity.get("filename")),
        content_type=_normalize_optional(entity.get("content_type")),
        audio_blob_path=_normalize_optional(entity.get("audio_blob_path")),
        soap_blob_path=_normalize_optional(entity.get("soap_blob_path")),
        transcript_blob_path=_normalize_optional(entity.get("transcript_blob_path")),
        session_at=_normalize_optional(entity.get("session_at")),
        created_at=_normalize_optional(entity.get("created_at")),
        updated_at=_normalize_optional(entity.get("updated_at")),
    )


# ── Blob helpers ─────────────────────────────────────────────────────────────

async def upload_session_blob(
    therapist_id: str,
    patient_id: str,
    session_id: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a session file to Azure Blob Storage using a therapist/patient/session prefix."""
    container_name = settings.azure_blob_container_name
    blob_path = f"{therapist_id}/{patient_id}/{session_id}/{filename}"

    logger.info("Uploading blob %s to container %s", blob_path, container_name)

    async with BlobServiceClient.from_connection_string(settings.azure_storage_connection_string) as service:
        container_client = service.get_container_client(container_name)
        try:
            await container_client.create_container()
        except Exception:
            pass

        await container_client.upload_blob(
            name=blob_path,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    return blob_path


async def upload_session_metadata(
    therapist_id: str,
    patient_id: str,
    session_id: str,
    metadata: Any,
) -> str:
    """Upload a small JSON metadata blob next to the session assets."""
    data = json.dumps(metadata, default=str).encode("utf-8")
    return await upload_session_blob(therapist_id, patient_id, session_id, "metadata.json", data, "application/json")


# ── Cosmos DB helper ──────────────────────────────────────────────────────────

async def _get_container():
    """Return (CosmosClient, ContainerProxy). Caller must call await client.close()."""
    client = CosmosClient(settings.cosmos_endpoint, settings.cosmos_key)
    db = await client.create_database_if_not_exists(id=settings.cosmos_db_name)
    container = await db.create_container_if_not_exists(
        id=settings.cosmos_sessions_container,
        partition_key=PartitionKey(path="/therapist_id"),
    )
    return client, container


# ── Azure Table helpers ──────────────────────────────────────────────────────

async def _ensure_sessions_table(table) -> None:
    try:
        await table.create_table()
    except Exception:
        pass


async def _query_sessions_table(table, filter_expression: str) -> list[dict[str, Any]]:
    entities = table.query_entities(filter_expression)
    return [e async for e in entities if e.get("entity_type") == "session"]


async def _get_session_entity_table(table, therapist_id: str, session_id: str) -> dict[str, Any]:
    try:
        return await table.get_entity(partition_key=therapist_id, row_key=session_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")


async def _download_docx_from_blob(docx_blob_path: str) -> str | None:
    """Download DOCX from blob storage and return as base64-encoded string.
    Returns None if the blob path is not set or download fails.
    """
    if not docx_blob_path:
        return None
    
    try:
        async with BlobServiceClient.from_connection_string(settings.azure_storage_connection_string) as service:
            container_client = service.get_container_client(settings.azure_blob_container_name)
            blob_client = container_client.get_blob_client(docx_blob_path)
            docx_bytes = await blob_client.download_blob()
            content = await docx_bytes.readall()
            return base64.b64encode(content).decode("utf-8")
    except Exception as exc:
        logger.warning("_download_docx_from_blob: failed to download %s — %s", docx_blob_path, str(exc))
        return None


# ── LIST ───────────────────────────────────────────────────────────────────────

@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List sessions for a therapist or all sessions (admin)",
)
async def list_sessions(
    request: Request,
    therapist_id: str | None = Query(None, description="Therapist ID (partition key). If omitted, admin can list all sessions."),
    patient_first_name: str | None = Query(None, description="Filter by patient first name"),
    patient_last_name: str | None = Query(None, description="Filter by patient last name"),
    include_docx: bool = Query(False, description="If true, download and include DOCX content as base64 for each session"),
):
    """When therapist_id is provided, returns sessions for that therapist.
    When therapist_id is omitted, only admin users can list all sessions across all therapists.
    """
    # Try to get user_id from state (set by middleware) or from header
    user_id = (getattr(request.state, "user_id", None) or request.headers.get("x-user-id") or "").strip().lower()
    
    if not therapist_id and not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either therapist_id or x-user-id must be provided.",
        )
    
    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            if therapist_id:
                if patient_first_name and patient_last_name:
                    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
                    items = container.query_items(
                        query="SELECT * FROM c WHERE c.therapist_id = @tid AND c.patient_id = @pid",
                        parameters=[{"name": "@tid", "value": therapist_id}, {"name": "@pid", "value": patient_id}],
                        partition_key=therapist_id,
                    )
                else:
                    items = container.query_items(
                        query="SELECT * FROM c WHERE c.therapist_id = @tid",
                        parameters=[{"name": "@tid", "value": therapist_id}],
                        partition_key=therapist_id,
                    )
                results = []
                async for item in items:
                    docx_content_base64 = None
                    if include_docx:
                        docx_content_base64 = await _download_docx_from_blob(item.get("soap_blob_path"))
                    results.append(SessionResponse(**item, docx_content_base64=docx_content_base64))
                return results
            else:
                # therapist_id is None; only admin can list all
                if user_id != "admin":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only admin can list all sessions.",
                    )
                # Return all sessions across all therapists
                items = container.query_items(query="SELECT * FROM c")
                results = []
                async for item in items:
                    docx_content_base64 = None
                    if include_docx:
                        docx_content_base64 = await _download_docx_from_blob(item.get("soap_blob_path"))
                    results.append(SessionResponse(**item, docx_content_base64=docx_content_base64))
                return results
        except HTTPException:
            raise
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")
        finally:
            await client.close()

    # Table Storage path
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(SESSIONS_TABLE_NAME)
            await _ensure_sessions_table(table)
            
            if therapist_id:
                filter_expression = f"PartitionKey eq '{therapist_id}'"
                if patient_first_name and patient_last_name:
                    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
                    filter_expression += f" and patient_id eq '{patient_id}'"
                entities = await _query_sessions_table(table, filter_expression)
                results = []
                for e in entities:
                    docx_content_base64 = None
                    if include_docx:
                        docx_content_base64 = await _download_docx_from_blob(e.get("soap_blob_path"))
                    response = _entity_to_session_response(e)
                    if docx_content_base64:
                        response.docx_content_base64 = docx_content_base64
                    results.append(response)
                return results
            else:
                # therapist_id is None; only admin can list all
                if user_id != "admin":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only admin can list all sessions.",
                    )
                # Return all sessions from the table
                filter_expression = "entity_type eq 'session'"
                entities = await _query_sessions_table(table, filter_expression)
                results = []
                for e in entities:
                    docx_content_base64 = None
                    if include_docx:
                        docx_content_base64 = await _download_docx_from_blob(e.get("soap_blob_path"))
                    response = _entity_to_session_response(e)
                    if docx_content_base64:
                        response.docx_content_base64 = docx_content_base64
                    results.append(response)
                return results
    except HTTPException:
        raise
    except HttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")


# ── GET BY PATIENT IDENTITY ────────────────────────────────────────────────────

@router.get(
    "/sessions/patient",
    response_model=list[SessionResponse],
    summary="List all sessions for a patient (identity derived from name hash)",
)
async def get_sessions_by_patient(
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
    include_docx: bool = Query(False, description="If true, download and include DOCX content as base64 for each session"),
):
    """Returns all session records belonging to the given therapist + patient combination.
    Patient identity is derived server-side via SHA-256 — names are never stored.
    """
    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            items = container.query_items(
                query="SELECT * FROM c WHERE c.therapist_id = @tid AND c.patient_id = @pid",
                parameters=[{"name": "@tid", "value": therapist_id}, {"name": "@pid", "value": patient_id}],
                partition_key=therapist_id,
            )
            results = []
            async for item in items:
                docx_content_base64 = None
                if include_docx:
                    docx_content_base64 = await _download_docx_from_blob(item.get("soap_blob_path"))
                results.append(SessionResponse(**item, docx_content_base64=docx_content_base64))
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")
        finally:
            await client.close()
    else:
        filter_expression = f"PartitionKey eq '{therapist_id}' and patient_id eq '{patient_id}'"
        try:
            async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
                table = service.get_table_client(SESSIONS_TABLE_NAME)
                await _ensure_sessions_table(table)
                entities = await _query_sessions_table(table, filter_expression)
                results = []
                for e in entities:
                    docx_content_base64 = None
                    if include_docx:
                        docx_content_base64 = await _download_docx_from_blob(e.get("soap_blob_path"))
                    response = _entity_to_session_response(e)
                    # Add DOCX content to response
                    if docx_content_base64:
                        response.docx_content_base64 = docx_content_base64
                    results.append(response)
        except HttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")

    if not results:
        raise HTTPException(status_code=404, detail="No sessions found for this patient.")
    return results


# ── PUT BY PATIENT IDENTITY ────────────────────────────────────────────────────

@router.put(
    "/sessions/patient",
    response_model=SessionResponse,
    summary="Update the most recent session for a patient (identity derived from name hash)",
)
async def update_session_by_patient(
    payload: SessionUpdate,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    """Finds the most recently created session for the given therapist + patient and
    applies a partial update. Patient identity is derived via SHA-256 — names are never stored.
    """
    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            items_iter = container.query_items(
                query=(
                    "SELECT * FROM c WHERE c.therapist_id = @tid AND c.patient_id = @pid"
                    " ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
                ),
                parameters=[{"name": "@tid", "value": therapist_id}, {"name": "@pid", "value": patient_id}],
                partition_key=therapist_id,
            )
            results = [item async for item in items_iter]
            if not results:
                raise HTTPException(status_code=404, detail="No sessions found for this patient.")
            item = results[0]
            updates = {k: v for k, v in payload.model_dump().items() if v is not None}
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            item.update(updates)
            await container.replace_item(item=item["id"], body=item)
            return SessionResponse(**item)
        except HTTPException:
            raise
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
        finally:
            await client.close()

    filter_expression = f"PartitionKey eq '{therapist_id}' and patient_id eq '{patient_id}'"
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(SESSIONS_TABLE_NAME)
            await _ensure_sessions_table(table)
            matches = await _query_sessions_table(table, filter_expression)
            if not matches:
                raise HTTPException(status_code=404, detail="No sessions found for this patient.")

            item = sorted(matches, key=lambda e: e.get("created_at", ""), reverse=True)[0]
            updates = {k: v for k, v in payload.model_dump().items() if v is not None}
            item.update(updates)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()

            await table.update_entity(mode="merge", entity=item)
            return _entity_to_session_response(item)
    except HTTPException:
        raise
    except HttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Table error: {exc.message}")


# ── GET ────────────────────────────────────────────────────────────────────────

@router.get(
    "/sessions/{id}",
    response_model=SessionResponse,
    summary="Get a session by ID (identity verified via patient name hash)",
)
async def get_session(
    id: str,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
    include_docx: bool = Query(False, description="If true, download and include DOCX content as base64"),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            item = await container.read_item(item=id, partition_key=therapist_id)
        except cosmos_exc.CosmosResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found.")
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
        finally:
            await client.close()
    else:
        try:
            async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
                table = service.get_table_client(SESSIONS_TABLE_NAME)
                await _ensure_sessions_table(table)
                item = await _get_session_entity_table(table, therapist_id, id)
        except HTTPException:
            raise
        except HttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Table error: {exc.message}")
        if item.get("entity_type") != "session":
            raise HTTPException(status_code=404, detail="Session not found.")

    if item.get("patient_id") != expected:
        raise HTTPException(status_code=403, detail="Patient identity mismatch.")
    
    # Fetch DOCX if requested
    docx_content_base64 = None
    if include_docx:
        docx_blob_path = item.get("soap_blob_path")
        docx_content_base64 = await _download_docx_from_blob(docx_blob_path)
    
    if settings.enable_cosmos_db:
        return SessionResponse(
            **item,
            docx_content_base64=docx_content_base64,
        )
    # For Table Storage, build response without internal Azure fields
    return SessionResponse(
        id=item.get("id") or item.get("session_id") or item.get("RowKey", ""),
        therapist_id=item.get("therapist_id", ""),
        patient_id=item.get("patient_id", ""),
        status=_normalize_optional(item.get("status")),
        filename=_normalize_optional(item.get("filename")),
        content_type=_normalize_optional(item.get("content_type")),
        audio_blob_path=_normalize_optional(item.get("audio_blob_path")),
        soap_blob_path=_normalize_optional(item.get("soap_blob_path")),
        transcript_blob_path=_normalize_optional(item.get("transcript_blob_path")),
        session_at=_normalize_optional(item.get("session_at")),
        created_at=_normalize_optional(item.get("created_at")),
        updated_at=_normalize_optional(item.get("updated_at")),
        docx_content_base64=docx_content_base64,
    )


# ── UPDATE ─────────────────────────────────────────────────────────────────────

@router.put(
    "/sessions/{id}",
    response_model=SessionResponse,
    summary="Partial update of a session record",
)
async def update_session(
    id: str,
    payload: SessionUpdate,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            item = await container.read_item(item=id, partition_key=therapist_id)
            if item.get("patient_id") != expected:
                raise HTTPException(status_code=403, detail="Patient identity mismatch.")
            updates = {k: v for k, v in payload.model_dump().items() if v is not None}
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            item.update(updates)
            await container.replace_item(item=id, body=item)
            return SessionResponse(**item)
        except HTTPException:
            raise
        except cosmos_exc.CosmosResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found.")
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
        finally:
            await client.close()

    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(SESSIONS_TABLE_NAME)
            await _ensure_sessions_table(table)
            item = await _get_session_entity_table(table, therapist_id, id)
            if item.get("entity_type") != "session":
                raise HTTPException(status_code=404, detail="Session not found.")
            if item.get("patient_id") != expected:
                raise HTTPException(status_code=403, detail="Patient identity mismatch.")

            updates = {k: v for k, v in payload.model_dump().items() if v is not None}
            item.update(updates)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()

            await table.update_entity(mode="merge", entity=item)
            return _entity_to_session_response(item)
    except HTTPException:
        raise
    except HttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Table error: {exc.message}")


# ── DELETE ─────────────────────────────────────────────────────────────────────

@router.delete(
    "/sessions/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session record",
)
async def delete_session(
    id: str,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            item = await container.read_item(item=id, partition_key=therapist_id)
            if item.get("patient_id") != expected:
                raise HTTPException(status_code=403, detail="Patient identity mismatch.")
            await container.delete_item(item=id, partition_key=therapist_id)
            return
        except HTTPException:
            raise
        except cosmos_exc.CosmosResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found.")
        except cosmos_exc.CosmosHttpResponseError as exc:
            raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
        finally:
            await client.close()

    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(SESSIONS_TABLE_NAME)
            await _ensure_sessions_table(table)
            item = await _get_session_entity_table(table, therapist_id, id)
            if item.get("entity_type") != "session":
                raise HTTPException(status_code=404, detail="Session not found.")
            if item.get("patient_id") != expected:
                raise HTTPException(status_code=403, detail="Patient identity mismatch.")

            await table.delete_entity(partition_key=therapist_id, row_key=id)
    except HTTPException:
        raise
    except HttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Table error: {exc.message}")


# ── CREATE SESSION (multipart: audio upload + patient info) ─────────────────

@router.post(
    "/sessions",
    response_model=SessionUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a therapy session — uploads audio, persists record, queues SOAP workflow",
)
async def create_session(
    background_tasks: BackgroundTasks,
    therapist_id: str = Form(...),
    patient_first_name: str = Form(..., description="Patient first name — hashed to patient_id, not stored (HIPAA)"),
    patient_last_name: str = Form(..., description="Patient last name — hashed to patient_id, not stored (HIPAA)"),
    session_at: str = Form(..., description="ISO 8601 session datetime"),
    file: UploadFile = File(...),
):
    """
    Single endpoint for creating a therapy session from the UI:
    1. Derives patient_id, no PII stored.
    2. Uploads audio to Azure Blob Storage under therapist/patient/session.
    3. Persists session document in Cosmos DB.
    4. Enqueues a LangGraph SOAP-note generation job.
    Returns the full session record plus a `job_id` for polling.
    """
    if file.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio type: {file.content_type}",
        )

    contents = await file.read()
    if len(contents) / (1024 * 1024) > settings.max_upload_size_mb:
        raise HTTPException(status_code=413, detail="File exceeds upload size limit.")

    original_filename = file.filename or "audio"
    mime = file.content_type or "audio/wav"
    session_id = str(uuid.uuid4())
    # Derive patient_id from hash — names are never written to storage (HIPAA)
    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)

    # 1. Upload audio to Azure Blob (skipped when ENABLE_BLOB_STORAGE=false)
    if settings.enable_blob_storage:
        audio_blob_path = await upload_session_blob(
            therapist_id=therapist_id,
            patient_id=patient_id,
            session_id=session_id,
            filename=original_filename,
            data=contents,
            content_type=mime,
        )
        # 2. Upload sidecar metadata blob (no patient PII)
        await upload_session_metadata(therapist_id, patient_id, session_id, {
            "therapist_id": therapist_id,
            "patient_id": patient_id,
            "session_at": session_at,
            "original_filename": original_filename,
            "audio_blob_path": audio_blob_path,
        })
        logger.info("create_session: audio uploaded to blob path %s", audio_blob_path)
    else:
        audio_blob_path = None
        logger.info("create_session: blob storage disabled — skipping audio upload")

    # 3. Persist session record in configured backend
    now = datetime.now(timezone.utc).isoformat()
    cosmos_record = {
        "id": session_id,
        "therapist_id": therapist_id,
        "patient_id": patient_id,
        "status": "uploaded",
        "filename": original_filename,
        "content_type": mime,
        "audio_blob_path": audio_blob_path,
        "soap_blob_path": None,
        "transcript_blob_path": None,
        "session_at": session_at,
        "created_at": now,
        "updated_at": now,
    }

    if settings.enable_cosmos_db:
        client, container = await _get_container()
        try:
            await container.create_item(body=cosmos_record)
            logger.info("create_session: session record persisted in Cosmos DB")
        except cosmos_exc.CosmosHttpResponseError as exc:
            logger.error("upload: Cosmos error — %s", exc.message)
            raise HTTPException(status_code=503, detail="Failed to persist session record.")
        finally:
            await client.close()
    else:
        table_record = {
            "PartitionKey": therapist_id,
            "RowKey": session_id,
            "entity_type": "session",
            "id": session_id,
            "therapist_id": therapist_id,
            "patient_id": patient_id,
            "status": "uploaded",
            "filename": original_filename,
            "content_type": mime,
            "audio_blob_path": audio_blob_path or "",
            "soap_blob_path": "",
            "transcript_blob_path": "",
            "session_at": session_at,
            "created_at": now,
            "updated_at": now,
        }
        try:
            async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
                table = service.get_table_client(SESSIONS_TABLE_NAME)
                await _ensure_sessions_table(table)
                await table.upsert_entity(entity=table_record)
                logger.info("create_session: session record persisted in Azure Table Storage")
        except HttpResponseError as exc:
            logger.error("create_session: Table error — %s", exc.message)
            raise HTTPException(status_code=503, detail="Failed to persist session record.")

    # 4. Enqueue LangGraph SOAP workflow
    job_id = str(uuid.uuid4())
    _job_store[job_id] = {"status": "queued", "step": None, "error": None, "session_id": session_id}

    from app.workflow.graph import run_workflow  # lazy import avoids circular deps
    background_tasks.add_task(
        run_workflow,
        job_id=job_id,
        therapist_id=therapist_id,
        client_id=patient_id,        # workflow param name kept for compatibility
        session_id=session_id,
        audio_bytes=contents,
        original_filename=original_filename,
        content_type=mime,
        job_store=_job_store,
    )

    return SessionUploadResponse(
        job_id=job_id,
        id=session_id,
        therapist_id=therapist_id,
        patient_id=patient_id,
        status="uploaded",
        filename=original_filename,
        content_type=mime,
        audio_blob_path=audio_blob_path,
        soap_blob_path=None,
        transcript_blob_path=None,
        session_at=session_at,
        created_at=now,
        updated_at=now,
        message="Audio accepted. SOAP note generation queued.",
    )


# ── JOB STATUS POLL ────────────────────────────────────────────────────────────

@router.get(
    "/sessions/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll SOAP workflow job status",
)
async def get_job_status(job_id: str):
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format.")
    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job with ID '{job_id}'.")
    return JobStatusResponse(job_id=job_id, **job)

