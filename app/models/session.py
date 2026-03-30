from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class SessionUpdate(BaseModel):
    """All fields optional — only provided fields are patched.
    Patient identity cannot be changed after creation.
    """
    filename: Optional[str] = None
    content_type: Optional[str] = None
    audio_blob_path: Optional[str] = None
    soap_blob_path: Optional[str] = None
    transcript_blob_path: Optional[str] = None
    session_at: Optional[str] = None


# ── Cosmos DB document / response (no PII) ────────────────────────────────────

class SessionResponse(BaseModel):
    """Document shape stored in Cosmos DB and returned by the API.
    Contains patient_id (SHA-256 hash) but never patient names.
    """
    session_id: str
    therapist_id: str
    patient_id: str = Field(..., description="SHA-256(therapist_id:first_name:last_name) — no PII stored")
    filename: Optional[str] = None
    content_type: Optional[str] = None
    audio_blob_path: Optional[str] = None
    soap_blob_path: Optional[str] = None
    transcript_blob_path: Optional[str] = None
    session_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionUploadResponse(SessionResponse):
    """Returned by POST /sessions — includes the queued SOAP job ID."""
    job_id: str
    message: str = "Audio accepted. SOAP note generation queued."


# ── SOAP workflow job tracking ─────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    status: str          # queued | running | completed | failed
    step: Optional[str] = None
    error: Optional[str] = None
    session_id: Optional[str] = None
