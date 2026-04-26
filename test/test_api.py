import os
import io
import hashlib
from azure.cosmos.exceptions import CosmosResourceNotFoundError

# ── Env vars must come before any app import ──────────────────────────────────
os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
os.environ.setdefault("AZURE_TABLE_CONNECTION_STRING",   "UseDevelopmentStorage=true")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT",  "https://example.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_API_KEY",   "testkey")
os.environ.setdefault("AZURE_SOAP_ENDPOINT",    "https://example.soap.azure.com/")
os.environ.setdefault("AZURE_SOAP_API_KEY",     "testkey")
os.environ.setdefault("COSMOS_ENDPOINT",        "https://fake.documents.azure.com:443/")
os.environ.setdefault("COSMOS_KEY",             "dGVzdA==")

# SSO provider config
os.environ.setdefault("AZURE_AD_CLIENT_ID",    "clientid")
os.environ.setdefault("AZURE_AD_TENANT_ID",    "tenantid")
os.environ.setdefault("AZURE_AD_REDIRECT_URI", "https://localhost/auth/callback")
os.environ.setdefault("GOOGLE_CLIENT_ID",      "googleid")
os.environ.setdefault("GOOGLE_REDIRECT_URI",   "https://localhost/google/callback")

# ── Stub LangGraph startup hooks BEFORE importing main.app ───────────────────
import app.workflow.checkpointer as _ckpt
import app.workflow.graph as _graph

async def _noop_init(): return None
async def _noop_close(cp=None): return None
def _noop_compile(cp=None): return None

_ckpt.init_checkpointer = _noop_init
_ckpt.close_checkpointer = _noop_close
_graph.compile_graph = _noop_compile

# ── Now import app ────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient

import app.routers.therapist as account_mod
import app.routers.sessions as sessions_mod
from main import app

# ── Test patient constants ────────────────────────────────────────────────────
_T_ID  = "therap1"
_P_FIRST = "John"
_P_LAST  = "Smith"
# Precompute the expected patient_id hash using the same algorithm as the router
_P_HASH = hashlib.sha256(
    f"{_T_ID}:{_P_FIRST.lower()}:{_P_LAST.lower()}".encode()
).hexdigest()

_FAKE_SESSION = {
    "id": "sess-1",
    "session_id": "sess-1",
    "therapist_id": _T_ID,
    "patient_id": _P_HASH,        # hash only — no PII
    "filename": "audio.wav",
    "content_type": "audio/wav",
    "audio_blob_path": f"{_T_ID}/{_P_HASH}/sess-1/audio.wav",
    "soap_blob_path": None,
    "transcript_blob_path": None,
    "session_at": "2026-03-29T10:00:00Z",
    "created_at": "2026-03-29T00:00:00+00:00",
    "updated_at": "2026-03-29T00:00:00+00:00",
}


# ── Cosmos DB dummy ───────────────────────────────────────────────────────────
class _DummyContainer:
    async def create_item(self, body):
        return body

    async def read_item(self, item, partition_key):
        if item != "sess-1":
            raise CosmosResourceNotFoundError(message="Not found", response=None)
        return dict(_FAKE_SESSION, id=item, session_id=item, therapist_id=partition_key)

    def query_items(self, query, parameters=None, partition_key=None):
        async def _gen():
            yield _FAKE_SESSION
        return _gen()

    async def upsert_item(self, body):
        return body

    async def replace_item(self, item, body):
        return body

    async def delete_item(self, item, partition_key):
        return None


class _DummyDB:
    async def create_container_if_not_exists(self, **kwargs):
        return _DummyContainer()


class _DummyCosmosClient:
    def __init__(self, *args, **kwargs): pass
    async def close(self): pass
    async def create_database_if_not_exists(self, **kwargs):
        return _DummyDB()


# ── Azure Table dummy (for account router) ────────────────────────────────────
class _DummyTable:
    async def create_table(self): return None
    async def create_entity(self, entity): return None


class _DummyTableService:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def get_table_client(self, name): return _DummyTable()

    @staticmethod
    def from_connection_string(conn):
        return _DummyTableService()


# ── Blob no-ops ───────────────────────────────────────────────────────────────
async def _fake_blob(therapist_id, patient_id, session_id, filename, data, content_type):
    return f"{therapist_id}/{patient_id}/{session_id}/{filename}"

async def _fake_meta(therapist_id, patient_id, session_id, metadata):
    return f"{therapist_id}/{patient_id}/{session_id}/metadata.json"


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_account_register():
    account_mod.TableServiceClient = _DummyTableService
    client = TestClient(app)
    # Required fields only (no initial_practice — that's optional)
    payload = {
        "first_name":     "Jane",
        "last_name":      "Doe",
        "email":          "jane@example.com",
        "password":       "secret123",
        "sex":            "Female",
        "gender":         "Woman",
        "date_of_birth":  "1985-06-15",
        "license_number": "LIC-WA-12345",
        "license_state":  "WA",
        "license_type":   "LCSW",
    }
    r = client.post("/api/v1/therapist", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "Doe"
    assert data["license_type"] == "LCSW"
    assert data["license_state"] == "WA"
    assert data["sex"] == "Female"
    assert data["gender"] == "Woman"
    assert data["date_of_birth"] == "1985-06-15"
    assert "password" not in data, "password must never be returned in the response"
    assert "therapist_id" in data


def test_list_providers():
    client = TestClient(app)
    r = client.get("/api/v1/auth/providers")
    assert r.status_code == 200
    providers = [p["provider"] for p in r.json()]
    assert "entra" in providers or "google" in providers


def test_session_create():
    """POST /sessions: multipart upload creates session; response has hashed patient_id, no PII."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    sessions_mod.upload_session_blob = _fake_blob
    sessions_mod.upload_session_metadata = _fake_meta
    client = TestClient(app)
    files = {"file": ("audio.wav", io.BytesIO(b"fake-audio"), "audio/wav")}
    data = {
        "therapist_id": _T_ID,
        "patient_first_name": _P_FIRST,
        "patient_last_name": _P_LAST,
        "session_at": "2026-03-29T10:00:00Z",
    }
    r = client.post("/api/v1/sessions", data=data, files=files)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["therapist_id"] == _T_ID
    assert j["patient_id"] == _P_HASH, "patient_id must be SHA-256 hash of therapist:first:last"
    assert "patient_first_name" not in j
    assert "patient_last_name" not in j
    assert "session_id" in j
    assert "job_id" in j
    assert "audio_blob_path" in j


def test_session_list():
    """GET /sessions: list without patient filter returns all sessions for therapist."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/sessions", params={"therapist_id": _T_ID})
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    assert items[0]["therapist_id"] == _T_ID


def test_session_list_filtered_by_patient():
    """GET /sessions: list with patient name filter should derive hash and narrow results."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/sessions", params={
        "therapist_id": _T_ID,
        "patient_first_name": _P_FIRST,
        "patient_last_name": _P_LAST,
    })
    assert r.status_code == 200, r.text


def test_session_get_by_patient():
    """GET /sessions/patient: returns list of sessions matching therapist + patient names."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/sessions/patient", params={
        "therapist_id": _T_ID,
        "patient_first_name": _P_FIRST,
        "patient_last_name": _P_LAST,
    })
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    assert items[0]["patient_id"] == _P_HASH
    assert items[0]["session_id"] == "sess-1"


def test_session_put_by_patient():
    """PUT /sessions/patient: updates most recent session for therapist + patient names."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.put(
        "/api/v1/sessions/patient",
        params={"therapist_id": _T_ID, "patient_first_name": _P_FIRST, "patient_last_name": _P_LAST},
        json={"soap_blob_path": f"{_T_ID}/{_P_HASH}/sess-1/soap.docx"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["patient_id"] == _P_HASH


def test_session_get():
    """GET /sessions/{id}: requires patient names; server verifies hash match."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/sessions/sess-1", params={
        "therapist_id": _T_ID,
        "patient_first_name": _P_FIRST,
        "patient_last_name": _P_LAST,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["session_id"] == "sess-1"
    assert data["patient_id"] == _P_HASH
    assert "patient_first_name" not in data


def test_session_get_wrong_patient():
    """GET /sessions/{id}: wrong patient names must be rejected with 403."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/sessions/sess-1", params={
        "therapist_id": _T_ID,
        "patient_first_name": "Wrong",
        "patient_last_name": "Person",
    })
    assert r.status_code == 403, r.text


def test_session_update():
    """PUT /sessions/{id}: requires patient names for identity verification."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.put(
        "/api/v1/sessions/sess-1",
        params={"therapist_id": _T_ID, "patient_first_name": _P_FIRST, "patient_last_name": _P_LAST},
        json={"soap_blob_path": f"{_T_ID}/{_P_HASH}/sess-1/soap.docx"},
    )
    assert r.status_code == 200, r.text


def test_session_delete():
    """DELETE /sessions/{id}: requires patient names for identity verification."""
    sessions_mod.CosmosClient = _DummyCosmosClient
    client = TestClient(app)
    r = client.delete("/api/v1/sessions/sess-1", params={
        "therapist_id": _T_ID,
        "patient_first_name": _P_FIRST,
        "patient_last_name": _P_LAST,
    })
    assert r.status_code == 204, r.text



