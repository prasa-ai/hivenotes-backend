from __future__ import annotations

from pydantic import BaseModel, Field


class SessionUpdate(BaseModel):
    """All fields optional — only provided fields are patched.
    Patient identity cannot be changed after creation.
    """
    status: str | None = None
    filename: str | None = None
    content_type: str | None = None
    audio_blob_path: str | None = None
    soap_blob_path: str | None = None
    transcript_blob_path: str | None = None
    session_at: str | None = None


# ── Cosmos DB document / response (no PII) ────────────────────────────────────

class SessionResponse(BaseModel):
    """Document shape stored in Cosmos DB and returned by the API.
    """
    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "uuid-session-123",
                "therapist_id": "jane.doe@example.com",
                "patient_id": "sha256-hash-here",
                "status": "uploaded",
                "session_at": "2026-04-26T10:30:00Z",
                "audio_blob_path": "jane.doe/.../recording.wav",
                "filename": "session_recording.wav",
                "content_type": "audio/wav",
                "soap_blob_path": None,
                "transcript_blob_path": None,
                "created_at": "2026-04-26T10:30:00Z",
                "updated_at": "2026-04-26T10:30:00Z",
                "docx_content_base64": None,
            }
        }
    }
    id: str
    therapist_id: str
    patient_id: str = Field(..., description="SHA-256(therapist_id:first_name:last_name) — no PII stored")
    status: str | None = None
    filename: str | None = None
    content_type: str | None = None
    audio_blob_path: str | None = None
    soap_blob_path: str | None = None
    transcript_blob_path: str | None = None
    session_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    docx_content_base64: str | None = Field(default=None, description="Base64-encoded DOCX file content (only populated when include_docx=true)")


class SessionUploadResponse(SessionResponse):
    """Returned by POST /sessions — includes the queued SOAP job ID."""
    job_id: str
    message: str = "Audio accepted. SOAP note generation queued."


# ── SOAP workflow job tracking ─────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    status: str          # queued | running | completed | failed
    step: str | None = None
    error: str | None = None
    session_id: str | None = None
