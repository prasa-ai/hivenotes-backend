from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class StartSessionRequest(BaseModel):
    device_id: Optional[str] = Field(None, description="App install / device fingerprint surrogate")
    app_version: Optional[str] = Field(None, description="Flutter app version string")
    platform: Optional[str] = Field(None, description="ios | android")
    os_version: Optional[str] = Field(None, description="Operating system version")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Any additional key-value pairs")


class StartSessionResponse(BaseModel):
    session_id: str
    user_id: str
    started_at: str


class EndSessionRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Optional reason for ending the session")


class SessionStatusResponse(BaseModel):
    session_id: str
    user_id: str
    status: str          # active | ended | expired
    started_at: str
    ended_at: Optional[str] = None
    platform: Optional[str] = None
    app_version: Optional[str] = None
    metadata: Dict[str, Any] = {}
