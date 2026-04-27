from __future__ import annotations

from pydantic import BaseModel, Field


class SessionUpdate(BaseModel):
    """All fields optional — only provided fields are patched.
    Patient identity cannot be changed after creation.
    """
    filename: str | None = None
    content_type: str | None = None
    audio_blob_path: str | None = None
    soap_blob_path: str | None = None
    transcript_blob_path: str | None = None
    session_at: str | None = None


# ── Cosmos DB document / response (no PII) ────────────────────────────────────

class SessionResponse(BaseModel):
    """Document shape stored in Cosmos DB and returned by the API.
    Contains patient_id (SHA-256 hash) but never patient names.
    """
    session_id: str
    therapist_id: str
    patient_id: str = Field(..., description="SHA-256(therapist_id:first_name:last_name) — no PII stored")
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
