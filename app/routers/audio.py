import uuid
import logging
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, status, BackgroundTasks
from app.models.audio import AudioUploadResponse, JobStatusResponse
from app.config import settings

router = APIRouter()

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/aac",
    "audio/ogg",
    "audio/webm",
    "audio/m4a",
    "audio/x-m4a",
}

# In-memory job status store — will be replaced by persistent store in later steps
_job_store: dict[str, dict] = {}

@router.post(
    "/audio/upload",
    response_model=AudioUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an audio file to trigger SOAP note generation",
)
async def upload_audio(
    background_tasks: BackgroundTasks,
    therapist_id: str = Form(..., description="Therapist identifier (e.g. therapist1)"),
    client_id: str = Form(..., description="Client identifier (e.g. client1)"),
    session_id: str = Form(..., description="Session identifier (e.g. session1)"),
    file: UploadFile = File(...),
):
    """
    Accept an audio recording from the mobile app and enqueue a LangGraph
    processing job that will:
    1. Fetch the blob folder path from Azure Table Storage
    2. Store the audio in Azure Blob Storage
    3. Transcribe via Azure AI Whisper or GPT-4o-mini
    4. Generate a SOAP note via Azure OpenAI GPT-4o
    5. Produce a .docx document
    6. Store the .docx back in Azure Blob Storage
    """
    # --- Validate content type --------------------------------------------------
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{file.content_type}'. "
                f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            ),
        )

    # --- Validate file size (read into memory once) ----------------------------
    logger.info(
        "Step 1: Received upload: therapist_id='%s'  client_id='%s'  session_id='%s'  filename='%s'  content_type='%s'",
        therapist_id, client_id, session_id, file.filename, file.content_type
    )
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size {size_mb:.1f} MB exceeds the {settings.max_upload_size_mb} MB limit.",
        )
    logger.info(
        "Step 1: File size: %.1f MB", size_mb
    )

    # --- Create job record  ----------------------------------------------------
    job_id = str(uuid.uuid4())
    _job_store[job_id] = {
        "status": "queued",
        "step": None,
        "error": None,
        "docx_blob_path": None,
    }

    logger.info("azure_gpt_mini_transcribe_api_key=%s azure_gpt_mini_transcribe_endpoint=%s", settings.azure_gpt_mini_transcribe_api_key, settings.azure_gpt_mini_transcribe_endpoint)
    logger.info("azure_openai_api_key=%s azure_openai_endpoint=%s", settings.azure_openai_api_key, settings.azure_openai_endpoint)
        

    # --- Enqueue background LangGraph workflow ---------------------------------
    # The workflow module will be wired in as each LangGraph node is implemented.
    # For now the background task is registered but the graph itself is a stub.
    from app.workflow.graph import run_workflow  # imported lazily to avoid circular deps
    background_tasks.add_task(
        run_workflow,
        job_id=job_id,
        therapist_id=therapist_id,
        client_id=client_id,
        session_id=session_id,
        audio_bytes=contents,
        original_filename=file.filename or "audio",
        content_type=file.content_type or "audio/wav",
        job_store=_job_store,
    )

    return AudioUploadResponse(
        job_id=job_id,
        therapist_id=therapist_id,
        client_id=client_id,
        session_id=session_id,
        original_filename=file.filename or "audio",
        size_bytes=len(contents),
        content_type=file.content_type or "audio/wav",
        status="queued",
        message="Audio file accepted. SOAP notes generation has been queued.",
    )


@router.get(
    "/audio/status/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll the status of a SOAP note generation job",
)
async def get_job_status(job_id: str):
    """
    Poll the processing status of a previously uploaded audio file.
    """
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format.",
        )

    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No job found with ID '{job_id}'.",
        )

    return JobStatusResponse(job_id=job_id, **job)

