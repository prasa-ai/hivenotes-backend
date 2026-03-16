from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AudioUploadResponse(BaseModel):
    """Returned immediately after the file is accepted by the API."""
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
    """Returned when polling /audio/status/{job_id}."""
    job_id: str
    status: str          # queued | running | completed | failed
    step: Optional[str] = None
    error: Optional[str] = None
    docx_blob_path: Optional[str] = None
