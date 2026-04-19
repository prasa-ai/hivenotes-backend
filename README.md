# HiveNotes Backend

A FastAPI + LangGraph backend that receives audio recordings of therapy sessions, transcribes them with Azure Whisper, generates structured SOAP notes via GPT-4o-mini, produces DOCX documents, and persists everything to Azure cloud storage.

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| Workflow orchestration | LangGraph |
| AI models | Azure OpenAI — Whisper (transcription), GPT-4o-mini (SOAP notes) |
| Storage | Azure Blob Storage, Azure Table Storage, Azure Cosmos DB (NoSQL) |
| Document generation | python-docx |

---

## Prerequisites

- Python **3.14+**
- An Azure account with the services listed in [Environment Variables](#environment-variables) provisioned

---

## Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd hivenotes-backend
```

### 2. Create a virtual environment

```bash
python3.14 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

For development tools (debugpy, etc.):

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root and populate it with your Azure credentials (see [Environment Variables](#environment-variables) below). The application validates all required variables at startup and exits with a clear error message if any are missing.

---

## Running the Application

### Development

```bash
python main.py
```

The API will be available at `http://localhost:8000`.

### Production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker

```bash
docker build -t hivenotes-backend .
docker-compose up
```

---

## API Documentation

With the server running, open the interactive docs in your browser:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

---

## API Endpoints

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok"}` |

### Account — Therapist management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/therapist` | Register a new therapist |
| `GET` | `/api/v1/therapist/{therapist_id}` | Get therapist details |
| `PUT` | `/api/v1/therapist/{therapist_id}` | Update therapist details |
| `DELETE` | `/api/v1/therapist/{therapist_id}` | Delete a therapist account |
| `GET` | `/api/v1/therapists?practice_id=` | List therapists in a practice |

### Auth — SSO

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/auth/providers` | List configured SSO providers (Entra, Google) with login URLs |
| `GET` | `/api/v1/auth/{provider}/callback` | OAuth2 callback stub (token exchange not yet implemented) |

### Sessions — Therapy session records

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sessions` | Upload audio + metadata; queues SOAP note generation |
| `GET` | `/api/v1/sessions?therapist_id=` | List sessions for a therapist (optional patient filter) |
| `GET` | `/api/v1/sessions/patient?therapist_id=&patient_first_name=&patient_last_name=` | List sessions for a specific patient |
| `PUT` | `/api/v1/sessions/patient?therapist_id=&patient_first_name=&patient_last_name=` | Update the most recent session for a patient |
| `GET` | `/api/v1/sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=` | Get a session by ID |
| `PUT` | `/api/v1/sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=` | Partially update a session |
| `DELETE` | `/api/v1/sessions/{session_id}?therapist_id=&patient_first_name=&patient_last_name=` | Delete a session |
| `GET` | `/api/v1/sessions/jobs/{job_id}` | Poll SOAP workflow job status |

> **Privacy note:** Patient names are never stored. All patient records are keyed by a SHA-256 hash derived from `therapist_id:first_name:last_name`.

### Quick cURL examples

```bash
# Register a therapist
curl -sS -X POST http://localhost:8000/api/v1/therapist \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","practice_id":"practice1","email":"jane@example.com","location":"Seattle","password":"s3cur3pass"}'

# List SSO providers
curl http://localhost:8000/api/v1/auth/providers

# Upload a session (starts SOAP note generation in background)
curl -X POST http://localhost:8000/api/v1/sessions \
  -H 'X-User-Id: jane@example.com' \
  -F therapist_id=jane@example.com \
  -F patient_first_name=John \
  -F patient_last_name=Smith \
  -F session_at=2026-03-29T10:00:00Z \
  -F "file=@/path/to/audio.wav;type=audio/wav"

# Poll job status
curl http://localhost:8000/api/v1/sessions/jobs/<job_id>
```

---

## Environment Variables

The application reads configuration from a `.env` file (or real environment variables). Required variables must be set; optional ones have sensible defaults.

| Variable | Description | Required |
|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Blob Storage — audio, transcripts, DOCX files | Yes |
| `AZURE_TABLE_CONNECTION_STRING` | Azure Table Storage — therapist records | Yes |
| `AZURE_SESSIONS_TABLE_CONNECTION_STRING` | Azure Table Storage — session index + LangGraph checkpoints | No* |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint (Whisper + GPT-4o-mini) | Yes |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | Yes |
| `AZURE_SOAP_ENDPOINT` | Separate Azure OpenAI endpoint for SOAP generation | Yes |
| `AZURE_SOAP_API_KEY` | API key for the SOAP generation resource | Yes |
| `AZURE_SOAP_API_VERSION` | API version for the SOAP deployment | No |
| `AZURE_GPT_MINI_TRANSCRIBE_ENDPOINT` | Azure OpenAI endpoint for GPT-4o-mini transcription | No |
| `AZURE_GPT_MINI_TRANSCRIBE_API_KEY` | API key for GPT-4o-mini transcription | No |
| `COSMOS_ENDPOINT` | Azure Cosmos DB (NoSQL) endpoint | Yes |
| `COSMOS_KEY` | Azure Cosmos DB primary key | Yes |
| `COSMOS_DB_NAME` | Cosmos DB database name (default: `hivenotes`) | No |
| `COSMOS_SESSIONS_CONTAINER` | Cosmos DB container name (default: `sessions`) | No |
| `AZURE_AD_CLIENT_ID` | Entra (Azure AD) app client ID for SSO login URL | No |
| `AZURE_AD_TENANT_ID` | Entra tenant ID | No |
| `AZURE_AD_REDIRECT_URI` | Entra OAuth2 redirect URI | No |
| `GOOGLE_CLIENT_ID` | Google OAuth2 client ID | No |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 client secret | No |
| `GOOGLE_REDIRECT_URI` | Google OAuth2 redirect URI | No |
| `MAX_UPLOAD_SIZE_MB` | Maximum audio upload size (default: `50`) | No |
| `USE_WHISPER_TRANSCRIPTION` | Use Whisper instead of GPT-4o-mini for transcription (default: `false`) | No |
| `SOAP_PROMPT_VERSION` | SOAP prompt variant — `v1_basic` or `v2_clinical` (default: `v2_clinical`) | No |
| `ENABLE_BLOB_STORAGE` | Enable Azure Blob uploads (default: `false` for local dev) | No |
| `ENABLE_COSMOS_DB` | Enable Cosmos DB persistence (default: `false` for local dev) | No |
| `ENABLE_CHECKPOINT` | Enable LangGraph checkpointing to Azure Table Storage (default: `false`) | No |

> \* Required when `ENABLE_CHECKPOINT=true`.

---

## Development

### Running tests

Tests mock all Azure services, so no cloud credentials are needed:

```bash
PYTHONPATH=. pytest -q
```

To run under Python 3.14 specifically (using the included venv):

```bash
source .venv314/bin/activate
PYTHONPATH=. python -m pytest -q
```

### Project structure

```
hivenotes-backend/
├── app/
│   ├── routers/           # API route handlers (account, auth, sessions)
│   ├── workflow/          # LangGraph graph, nodes, state, checkpointer
│   │   ├── nodes/         # Individual workflow steps (transcribe, SOAP, DOCX, …)
│   │   └── prompts/       # SOAP prompt variants (v1_basic, v2_clinical)
│   ├── models/            # Pydantic request/response models
│   ├── services/          # Azure Blob Storage helpers
│   ├── config.py          # Pydantic settings (reads .env)
│   └── dependencies.py    # FastAPI dependency injection
├── test/
│   └── test_api.py        # Full test suite (mocked Azure)
├── main.py                # Application entry point + lifespan
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Development-only dependencies
├── Dockerfile
└── docker-compose.yml
```

---

## Troubleshooting

**Missing environment variables** — The app exits at startup with a list of every missing variable. Add them to your `.env` file.

**Port already in use** — Change the port:

```bash
uvicorn main:app --port 8001
```

**Azure connection errors** — Verify your connection strings and that the relevant Azure services (Blob, Table, Cosmos DB, OpenAI) are provisioned and accessible from your network.

**`X-User-Id` header** — Development mode trusts the `X-User-Id` request header as a user identifier. Replace this with proper Bearer token validation before deploying to production.

---

## Contributing

Follow existing code patterns, keep all tests passing (`pytest -q`), and ensure type annotations use Python 3.10+ syntax (`X | None`, `list[T]`, etc.).

## License

[Add license information if applicable]
