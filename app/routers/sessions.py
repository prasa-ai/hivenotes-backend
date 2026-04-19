"""
Sessions router — Cosmos DB (NoSQL) CRUD for therapy session records.

Document schema (Cosmos DB container: sessions, partition key: /therapist_id)
─────────────────────────────────────────────────────────────────────────────
  id                   : patient_id (SHA-256 hash — Cosmos DB document key)
  therapist_id         : partition key
  patient_id           : str  (same as id)
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
  GET    /sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=   — get session
  PUT    /sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=   — partial update
  DELETE /sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=   — delete
  GET    /sessions/jobs/{job_id}                       — poll SOAP workflow job status
"""
import logging
import uuid
from datetime import datetime, timezone

import hashlib

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from azure.cosmos.aio import CosmosClient
from azure.cosmos import PartitionKey
from azure.cosmos import exceptions as cosmos_exc

from app.config import settings
from app.models.session import SessionResponse, SessionUpdate, JobStatusResponse, SessionUploadResponse
from app.services.azure_blob import upload_session_blob, upload_session_metadata

logger = logging.getLogger(__name__)
router = APIRouter()

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


# ── LIST ───────────────────────────────────────────────────────────────────────

@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List sessions for a therapist, optionally filtered to a patient",
)
async def list_sessions(
    therapist_id: str = Query(..., description="Therapist ID (partition key)"),
    patient_first_name: str | None = Query(None, description="Filter by patient first name"),
    patient_last_name: str | None = Query(None, description="Filter by patient last name"),
):
    """When patient_first_name and patient_last_name are both supplied the list is
    filtered to sessions belonging to that patient (identified by their hash).
    """
    client, container = await _get_container()
    try:
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
        results = [SessionResponse(**item) async for item in items]
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")
    finally:
        await client.close()
    return results


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
):
    """Returns all session records belonging to the given therapist + patient combination.
    Patient identity is derived server-side via SHA-256 — names are never stored.
    """
    patient_id = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
    client, container = await _get_container()
    try:
        items = container.query_items(
            query="SELECT * FROM c WHERE c.therapist_id = @tid AND c.patient_id = @pid",
            parameters=[{"name": "@tid", "value": therapist_id}, {"name": "@pid", "value": patient_id}],
            partition_key=therapist_id,
        )
        results = [SessionResponse(**item) async for item in items]
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Query failed: {exc.message}")
    finally:
        await client.close()
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
    client, container = await _get_container()
    try:
        # Query all sessions for this patient, ordered by created_at desc
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
    except HTTPException:
        raise
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
    finally:
        await client.close()
    return SessionResponse(**item)


# ── GET ────────────────────────────────────────────────────────────────────────

@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get a session by ID (identity verified via patient name hash)",
)
async def get_session(
    session_id: str,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
    client, container = await _get_container()
    try:
        item = await container.read_item(item=session_id, partition_key=therapist_id)
    except cosmos_exc.CosmosResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
    finally:
        await client.close()
    if item.get("patient_id") != expected:
        raise HTTPException(status_code=403, detail="Patient identity mismatch.")
    return SessionResponse(**item)


# ── UPDATE ─────────────────────────────────────────────────────────────────────

@router.put(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Partial update of a session record",
)
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
    client, container = await _get_container()
    try:
        item = await container.read_item(item=session_id, partition_key=therapist_id)
        if item.get("patient_id") != expected:
            raise HTTPException(status_code=403, detail="Patient identity mismatch.")
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        item.update(updates)
        await container.replace_item(item=session_id, body=item)
    except HTTPException:
        raise
    except cosmos_exc.CosmosResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
    finally:
        await client.close()
    return SessionResponse(**item)


# ── DELETE ─────────────────────────────────────────────────────────────────────

@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session record",
)
async def delete_session(
    session_id: str,
    therapist_id: str = Query(...),
    patient_first_name: str = Query(...),
    patient_last_name: str = Query(...),
):
    expected = _hash_patient_id(therapist_id, patient_first_name, patient_last_name)
    client, container = await _get_container()
    try:
        item = await container.read_item(item=session_id, partition_key=therapist_id)
        if item.get("patient_id") != expected:
            raise HTTPException(status_code=403, detail="Patient identity mismatch.")
        await container.delete_item(item=session_id, partition_key=therapist_id)
    except HTTPException:
        raise
    except cosmos_exc.CosmosResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")
    except cosmos_exc.CosmosHttpResponseError as exc:
        raise HTTPException(status_code=503, detail=f"Cosmos error: {exc.message}")
    finally:
        await client.close()


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
    1. Derives patient_id = SHA-256(therapist_id:first:last) — no PII stored.
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

    # 3. Persist session record in Cosmos DB (skipped when ENABLE_COSMOS_DB=false)
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": session_id,
        "session_id": session_id,
        "therapist_id": therapist_id,
        "patient_id": patient_id,
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
            await container.create_item(body=record)
            logger.info("create_session: session record persisted in Cosmos DB")
        except cosmos_exc.CosmosHttpResponseError as exc:
            logger.error("upload: Cosmos error — %s", exc.message)
            raise HTTPException(status_code=503, detail="Failed to persist session record.")
        finally:
            await client.close()
    else:
        logger.info("create_session: Cosmos DB disabled — skipping session record persistence")

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
        session_id=session_id,
        therapist_id=therapist_id,
        patient_id=patient_id,
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

