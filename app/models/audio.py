"""Legacy audio models — kept for import compatibility. Prefer app.models.session."""
from __future__ import annotations
 
from pydantic import BaseModel


class AudioUploadResponse(BaseModel):
    job_id: str
    therapist_id: str
    client_id: str
    session_id: str
    original_filename: str
    size_bytes: int
    content_type: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    step: str | None = None
    error: str | None = None
    docx_blob_path: str | None = None
