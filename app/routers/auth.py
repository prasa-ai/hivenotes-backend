import logging
import uuid
import urllib.parse
from typing import Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class ProviderInfo(BaseModel):
    provider: str
    login_url: str


@router.get("/auth/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """Return available SSO providers and a client-usable login URL for each."""
    providers: list[Dict[str, str]] = []

    # Entra (Azure AD) — build authorize URL if config present
    if settings.azure_ad_client_id and settings.azure_ad_tenant_id and settings.azure_ad_redirect_uri:
        authority = f"https://login.microsoftonline.com/{settings.azure_ad_tenant_id}/oauth2/v2.0/authorize"
        params = {
            "client_id": settings.azure_ad_client_id,
            "response_type": "code",
            "redirect_uri": settings.azure_ad_redirect_uri,
            "response_mode": "query",
            "scope": "openid profile email",
            "state": str(uuid.uuid4()),
        }
        providers.append({"provider": "entra", "login_url": authority + "?" + urllib.parse.urlencode(params)})

    # Google OAuth2
    if settings.google_client_id and settings.google_redirect_uri:
        google_auth = "https://accounts.google.com/o/oauth2/v2/auth"
        params = {
            "client_id": settings.google_client_id,
            "response_type": "code",
            "redirect_uri": settings.google_redirect_uri,
            "scope": "openid email profile",
            "state": str(uuid.uuid4()),
            "access_type": "offline",
            "prompt": "consent",
        }
        providers.append({"provider": "google", "login_url": google_auth + "?" + urllib.parse.urlencode(params)})

    if not providers:
        raise HTTPException(status_code=501, detail="No SSO providers configured on the server.")

    return [ProviderInfo(**p) for p in providers]


@router.get("/auth/{provider}/callback")
async def auth_callback(provider: str, code: str | None = None, state: str | None = None):
    """Callback endpoint where external SSO providers will redirect with a code.

    This handler currently returns a simple JSON acknowledging the auth code.
    Implement token exchange here using MSAL (for Entra) or Google's token endpoint when ready.
    """
    return {"provider": provider, "code": code, "state": state}
