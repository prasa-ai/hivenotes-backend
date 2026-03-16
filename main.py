from contextlib import asynccontextmanager
import logging
import logging.config

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from app.routers import audio
from app.routers import sessions
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
    "azure_sessions_table_connection_string":  "AZURE_SESSIONS_TABLE_CONNECTION_STRING",
    "azure_openai_endpoint":                   "AZURE_OPENAI_ENDPOINT",
    "azure_openai_api_key":                    "AZURE_OPENAI_API_KEY",
    "azure_soap_endpoint":                     "AZURE_SOAP_ENDPOINT",
    "azure_soap_api_key":                      "AZURE_SOAP_API_KEY",
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
    title="SOAP Notes API",
    description=(
        "FastAPI + LangGraph backend that receives an audio file from the mobile app "
        "and orchestrates transcription (Azure Whisper), SOAP note generation (GPT-4o), "
        "DOCX creation, and Azure Blob Storage persistence."
    ),
    version="0.3.0",
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
    Read the X-Session-Id header sent by the Flutter app on every request
    and attach it to request.state so any route handler can access it.
    Also attaches X-User-Id for development; replace with token validation
    in production.
    """
    request.state.session_id = request.headers.get("X-Session-Id")
    # Production: extract user_id from validated Bearer token here.
    # Dev fallback: trust the X-User-Id header (remove before going live).
    request.state.user_id = request.headers.get("X-User-Id")
    response = await call_next(request)
    return response


app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
app.include_router(audio.router,    prefix="/api/v1", tags=["audio"])


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
    schema["components"]["securitySchemes"]["X-User-Id"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-User-Id",
        "description": "Developer user ID (replaces auth token in local testing)",
    }

    # Apply the security scheme globally so every endpoint shows the lock icon
    schema["security"] = [{"X-User-Id": []}]

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}

