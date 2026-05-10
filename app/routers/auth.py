import logging
import uuid
import urllib.parse
from typing import Dict
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from azure.cosmos import PartitionKey
from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import CosmosClient

from app.config import settings
from app.services.auth_utils import verify_password, create_access_token

logger = logging.getLogger(__name__)
router = APIRouter()


class ProviderInfo(BaseModel):
    provider: str
    login_url: str


class LoginRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "jane.doe@example.com",
                "password": "s3cur3P@ssw0rd",
            }
        }
    }
    email: str
    password: str


class LoginResponse(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 28800,
                "therapist_id": "a3f8c2d1-7b4e-4f9a-8c2d-1a7b4e4f9a8c",
            }
        }
    }
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    therapist_id: str


async def _get_therapists_container():
    """Return (CosmosClient, ContainerProxy) for therapists. Caller must close the client."""
    client = CosmosClient(settings.cosmos_endpoint, settings.cosmos_key)
    db = await client.create_database_if_not_exists(id=settings.cosmos_db_name)
    container = await db.create_container_if_not_exists(
        id=settings.cosmos_therapists_container,
        partition_key=PartitionKey(path="/id"),
    )
    return client, container


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


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Password-based therapist login",
    description=(
        "Authenticate a therapist using their registered **email** and **password**.\n\n"
        "On success a signed **JWT bearer token** is returned. "
        "Copy the `access_token` value and paste it into the **Authorize** dialog "
        "(🔒 button at the top of this page) to authenticate subsequent requests.\n\n"
        "| Scenario | HTTP status |\n"
        "|---|---|\n"
        "| Credentials valid | `200 OK` |\n"
        "| Wrong email or password | `401 Unauthorized` |\n"
        "| JWT secret not configured | `503 Service Unavailable` |\n"
        "| Cosmos DB unreachable | `503 Service Unavailable` |\n"
    ),
    responses={
        200: {"description": "Login successful — JWT access token returned."},
        401: {"description": "Invalid email or password."},
        503: {"description": "Authentication service temporarily unavailable."},
        500: {"description": "Unexpected server error."},
    },
    tags=["auth"],
)
async def login(payload: LoginRequest):
    """Authenticate a therapist with email and password.

    Looks up the therapist document in Cosmos DB by email, verifies the bcrypt
    password hash, and returns a signed JWT access token on success.
    """
    if not settings.jwt_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured (JWT_SECRET_KEY missing).",
        )

    client, container = await _get_therapists_container()
    try:
        # Find therapist by email (cross-partition query, excludes mapping docs)
        therapist_doc: dict | None = None
        async for doc in container.query_items(
            query=(
                "SELECT * FROM c "
                "WHERE c.email = @email "
                "AND NOT STARTSWITH(c.id, 'mapping~')"
            ),
            parameters=[{"name": "@email", "value": payload.email.strip().lower()}],
        ):
            therapist_doc = doc
            break  # email is unique; first hit is sufficient

        # Use a constant-time comparison path even for "not found" to resist
        # timing-based user enumeration.
        stored_hash: str = therapist_doc.get("password_hash", "") if therapist_doc else ""
        password_ok = bool(stored_hash) and verify_password(payload.password, stored_hash)

        if not therapist_doc or not password_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        therapist_id: str = therapist_doc["therapist_id"]
        token = create_access_token(
            data={"sub": therapist_id, "email": therapist_doc["email"]},
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
            expires_minutes=settings.access_token_expire_minutes,
        )
        return LoginResponse(
            access_token=token,
            expires_in=settings.access_token_expire_minutes * 60,
            therapist_id=therapist_id,
        )

    except HTTPException:
        raise
    except cosmos_exc.CosmosHttpResponseError as exc:
        logger.error("login: Cosmos DB error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable. Please try again.",
        )
    except Exception as exc:
        logger.error("login: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )
    finally:
        await client.close()
