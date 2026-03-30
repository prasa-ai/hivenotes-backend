# Hivenotes Backend — Quick Start & Examples

This project exposes a small FastAPI backend that supports:

- Therapist registration (`/api/v1/account/register`)
- SSO provider discovery (`/api/v1/auth/providers`) and callback stubs
- Patient session upload including audio and metadata (`/api/v1/patient/session/upload`)

## Prerequisites

- Python 3.11+
- Install runtime dependencies:

```bash
pip install -r requirements.txt
```

## Run the app

```bash
python -m uvicorn main:app --reload --port {port_number}
```

## API examples

1. Register a therapist

```bash
curl -sS -X POST http://localhost:8000/api/v1/account/register \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","practice_id":"practice1","email":"jane@example.com","location":"Seattle"}'
```

2. List available SSO providers (Entra / Google)

```bash
curl http://localhost:8000/api/v1/auth/providers
```

3. Upload a patient session (multipart form)

```bash
curl -X POST http://localhost:8000/api/v1/patient/session/upload \
  -F therapist_id=therap1 \
  -F patient_first_name=John \
  -F patient_last_name=Smith \
  -F session_at=2026-03-29T10:00:00Z \
  -F "file=@/path/to/audio.wav;type=audio/wav"
```

## Tests

Install test dependencies and run pytest:

```bash
pip install pytest
pytest -q
```

## Notes

- The tests use mocks to avoid contacting Azure services; to run the server for real you must set the required Azure environment variables.
- The `/api/v1/auth/{provider}/callback` endpoint is a stub — token exchange and session creation are not implemented yet.

# HiveNotes Backend

A FastAPI + LangGraph backend for SOAP notes generation that orchestrates audio transcription, note generation, and document creation using Azure AI services.

## Overview

This application receives audio files, transcribes them using Azure Whisper, generates SOAP notes using GPT-4o models, creates DOCX documents, and persists them to Azure Blob Storage.

**Tech Stack:**

- **Web Framework:** FastAPI + Uvicorn
- **Workflow Orchestration:** LangGraph
- **AI Models:** Azure OpenAI (Whisper, GPT-4o-mini)
- **Storage:** Azure Blob Storage, Azure Table Storage
- **Document Generation:** python-docx

## Prerequisites

- Python 3.10+
- pip package manager
- Azure account with configured AI services and storage

## Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd hivenotes-backend
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Activate on macOS/Linux
source venv/bin/activate

# Activate on Windows
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

For development, also install dev dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 4. Configure Environment Variables

Copy the example environment file and add your Azure credentials:

```bash
cp .env.example .env
```

Edit `.env` and fill in the required Azure credentials:

- `AZURE_STORAGE_CONNECTION_STRING`
- `AZURE_TABLE_CONNECTION_STRING`
- `AZURE_SESSIONS_TABLE_CONNECTION_STRING`
- `AZURE_OPENAI_ENDPOINT` & `AZURE_OPENAI_API_KEY`
- `AZURE_SOAP_ENDPOINT` & `AZURE_SOAP_API_KEY`
- `AZURE_GPT_MINI_TRANSCRIBE_ENDPOINT` & `AZURE_GPT_MINI_TRANSCRIBE_API_KEY`

See `.env.example` for a complete list of configuration options.

## Running the Application

### Development Mode

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Production Mode

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Using Docker

```bash
docker build -t hivenotes-backend .
docker-compose up
```

## API Documentation

Once the server is running, access the interactive API documentation at:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## API Endpoints

### Health Check

```bash
GET /health
```

### Audio Processing

```bash
POST /api/v1/audio/process
```

Accepts audio files and returns transcribed SOAP notes.

### Sessions

```bash
GET /api/v1/sessions
POST /api/v1/sessions
```

Manage user sessions for SOAP note generation.

## Development

### Running Tests

```bash
pytest test/
```

### Code Quality

Ensure code quality with installed dev tools:

```bash
# Format code (if configured)
black .

# Lint code (if configured)
flake8 .
```

## Environment Variables

| Variable                                 | Description                                      | Required |
| ---------------------------------------- | ------------------------------------------------ | -------- |
| `AZURE_STORAGE_CONNECTION_STRING`        | Azure Blob Storage connection string             | Yes      |
| `AZURE_TABLE_CONNECTION_STRING`          | Azure Table Storage (folder mapping)             | Yes      |
| `AZURE_SESSIONS_TABLE_CONNECTION_STRING` | Azure Table Storage (sessions)                   | Yes      |
| `AZURE_OPENAI_ENDPOINT`                  | Azure OpenAI endpoint                            | Yes      |
| `AZURE_OPENAI_API_KEY`                   | Azure OpenAI API key                             | Yes      |
| `AZURE_SOAP_ENDPOINT`                    | Azure SOAP generation endpoint                   | Yes      |
| `AZURE_SOAP_API_KEY`                     | Azure SOAP generation API key                    | Yes      |
| `MAX_UPLOAD_SIZE_MB`                     | Maximum upload size (default: 50MB)              | No       |
| `USE_WHISPER_TRANSCRIPTION`              | Force Whisper for transcription (default: false) | No       |

## Project Structure

```
hivenotes-backend/
├── app/
│   ├── routers/           # API route handlers
│   ├── workflow/          # LangGraph workflow logic
│   ├── config.py          # Configuration & settings
│   └── ...
├── test/                  # Test suite
├── main.py                # Application entry point
├── requirements.txt       # Python dependencies
├── Dockerfile             # Docker configuration
└── docker-compose.yml     # Docker Compose setup
```

## Troubleshooting

### Missing Environment Variables

Ensure all required environment variables are set. The application will fail at startup if any are missing. Check `main.py` for the complete list of required variables.

### Azure Connection Issues

Verify your Azure credentials and ensure your Azure services are properly configured and accessible.

### Port Already in Use

Change the port using:

```bash
python main.py --port 8001
# or with uvicorn
uvicorn main:app --port 8001
```

## Contributing

Follow the existing code patterns and ensure all tests pass before submitting changes.

## License

[Add license information if applicable]
