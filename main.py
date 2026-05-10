from contextlib import asynccontextmanager
import logging
import logging.config

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from app.dependencies import require_auth
from app.routers import sessions
from app.routers import therapist
from app.routers import auth
from app.routers import patient
from app.workflow.checkpointer import init_checkpointer, close_checkpointer
from app.workflow.graph import compile_graph
from app.config import settings

# ── Logging — make sure all app loggers print to stdout alongside uvicorn ─────
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,          # keep uvicorn's own loggers intact
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "default",
        }
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
    # Silence noisy Azure SDK debug chatter unless you need it
    "loggers": {
        "azure": {"level": "WARNING"},
        "urllib3": {"level": "WARNING"},
    },
})

logger = logging.getLogger(__name__)

# Required env vars → friendly name shown in the error message
_REQUIRED_SETTINGS = {
    "azure_storage_connection_string":         "AZURE_STORAGE_CONNECTION_STRING",
    "azure_table_connection_string":           "AZURE_TABLE_CONNECTION_STRING",
    "azure_openai_endpoint":                   "AZURE_OPENAI_ENDPOINT",
    "azure_openai_api_key":                    "AZURE_OPENAI_API_KEY",
    "azure_soap_endpoint":                     "AZURE_SOAP_ENDPOINT",
    "azure_soap_api_key":                      "AZURE_SOAP_API_KEY",
    "cosmos_endpoint":                         "COSMOS_ENDPOINT",
    "cosmos_key":                              "COSMOS_KEY",
}


def _validate_settings() -> None:
    missing = [
        env_var
        for attr, env_var in _REQUIRED_SETTINGS.items()
        if not getattr(settings, attr, "")
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables — add them to your .env file:\n"
            + "\n".join(f"  {v}" for v in missing)
        )
    logger.info("startup: all required settings present.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_settings()
    # Startup: connect the checkpointer then compile the graph with it attached.
    checkpointer = await init_checkpointer()
    compile_graph(checkpointer)
    yield
    # Shutdown: close Azure connections gracefully.
    await close_checkpointer()


app = FastAPI(
    title="HiveNotes API",
    description=(
        "FastAPI + LangGraph backend for therapy session management and SOAP note generation.\n\n"
        "## Modules\n"
        "- **account** — Therapist registration and profile management. "
        "Captures real-world identity (licence number, state, type) separately from "
        "practice tenancy, which is managed via a mapping table.\n"
        "- **auth** — SSO provider discovery (Entra / Google) and OAuth2 callback stubs.\n"
        "- **sessions** — Upload audio recordings, trigger SOAP note generation, "
        "and retrieve session records.\n"
        "- **health** — Liveness probe.\n\n"
        "## Authentication\n"
        "Call **POST /api/v1/auth/login** with your email and password to receive a JWT.\n"
        "Then click the **Authorize** button (🔒) and paste the `access_token` value "
        "into the **BearerAuth** field. All endpoints except the auth routes require a valid token."
    ),
    version="0.4.0",
    openapi_tags=[
        {
            "name": "account",
            "description": (
                "Therapist registration and profile management. "
                "Licence details (`license_number`, `license_state`, `license_type`) "
                "are the source of truth for *who the therapist is* legally. "
                "Practice membership is managed separately via a mapping table so a "
                "therapist can work at multiple practices."
            ),
        },
        {
            "name": "auth",
            "description": (
                "Authentication endpoints.\\n\\n"
                "**Password login** — `POST /api/v1/auth/login` accepts an email and password, "
                "verifies the bcrypt hash stored at registration, and returns a signed JWT bearer token. "
                "Copy the `access_token` and use the **Authorize** button (🔒) to authenticate "
                "all subsequent requests.\\n\\n"
                "**SSO provider discovery** — returns login URLs for configured Entra and Google providers. "
                "Token exchange is a stub — not yet implemented."
            ),
        },
        {
            "name": "sessions",
            "description": (
                "Therapy session lifecycle: upload audio, trigger SOAP note generation "
                "via LangGraph, and retrieve session records. "
                "Patient PII is never persisted — identity is derived from a "
                "SHA-256 hash of `therapist_id:first_name:last_name`."
            ),
        },
        {
            "name": "health",
            "description": "Liveness probe used by load balancers and container orchestrators.",
        },
    ],
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_session_context(request: Request, call_next):
    """
    Populates request.state.session_id and request.state.user_id for every request.
    user_id is decoded from the JWT Bearer token so route handlers that read it
    work without an extra Depends call.
    """
    from jose import jwt as _jwt, JWTError

    request.state.session_id = request.headers.get("X-Session-Id")

    user_id: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and settings.jwt_secret_key:
        try:
            payload = _jwt.decode(
                auth_header[7:],
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
            )
            user_id = payload.get("sub")
        except JWTError:
            pass

    request.state.user_id = user_id
    response = await call_next(request)
    return response


from fastapi import Depends

app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"], dependencies=[Depends(require_auth)])
app.include_router(therapist.router, prefix="/api/v1", tags=["account"], dependencies=[Depends(require_auth)])
app.include_router(auth.router,      prefix="/api/v1", tags=["auth"])  # no auth required


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}


# ── Custom OpenAPI schema: expose X-User-Id as an API key in Swagger UI ───────
# This adds an "Authorize" button to /docs where you can enter a user ID
# and have it sent automatically as the X-User-Id header on every request.
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "JWT obtained from **POST /api/v1/auth/login**. "
            "Paste the `access_token` value here."
        ),
    }
    # Remove the auto-generated HTTPBearer scheme FastAPI adds via Depends(http_bearer)
    # so Swagger only shows one unified "BearerAuth" scheme.
    schema["components"]["securitySchemes"].pop("HTTPBearer", None)

    # Apply BearerAuth globally — then clear it on public (auth + health) routes.
    schema["security"] = [{"BearerAuth": []}]

    _PUBLIC_PATHS = {
        "/api/v1/auth/login",
        "/api/v1/auth/providers",
        "/health",
    }
    for path, path_item in schema.get("paths", {}).items():
        is_public = path in _PUBLIC_PATHS or path.startswith("/api/v1/auth/{provider}")
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            if is_public:
                operation["security"] = []
            else:
                # Replace FastAPI's auto-generated HTTPBearer with our named BearerAuth
                op_security = operation.get("security", [])
                operation["security"] = [
                    {"BearerAuth": []} if list(s.keys()) == ["HTTPBearer"] else s
                    for s in op_security
                ] or [{"BearerAuth": []}]

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

