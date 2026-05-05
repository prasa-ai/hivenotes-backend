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

# ── Therapist fake data ──────────────────────────────────────────────────────
_FAKE_THERAPIST_ID = "therapist-uuid-1"
_FAKE_THERAPIST = {
    "id":                  _FAKE_THERAPIST_ID,
    "therapist_id":        _FAKE_THERAPIST_ID,
    "reference_id":        "TH-001",
    "first_name":          "Jane",
    "last_name":           "Doe",
    "email":               "jane@example.com",
    "sex":                 "Female",
    "gender":              "Woman",
    "date_of_birth":       "1985-06-15",
    "license_type":        "LCSW",
    "license_state":       "WA",
    "license_number":      "LIC-WA-12345",
    "npi_number":          "",
    "years_of_experience": "",
    "specialization":      "",
    "profile_picture_url": "",
    "created_at":          "2026-01-01T00:00:00+00:00",
    "updated_at":          "2026-01-01T00:00:00+00:00",
}


class _DummyTherapistContainer:
    """Cosmos DB container stub for therapist tests.

    Set ``_query_results`` to control what ``query_items`` yields:
    - Empty list (default) → email-uniqueness check finds no conflicts.
    - [_FAKE_THERAPIST]   → admin list returns the fake doc.
    """
    _query_results: list = []

    async def create_item(self, body):
        return body

    async def read_item(self, item, partition_key):
        if item == _FAKE_THERAPIST_ID:
            return dict(_FAKE_THERAPIST)
        raise CosmosResourceNotFoundError(message="Not found", response=None)

    def query_items(self, query, parameters=None, partition_key=None):
        results = list(_DummyTherapistContainer._query_results)
        async def _gen():
            for r in results:
                yield r
        return _gen()

    async def upsert_item(self, body):
        return body

    async def replace_item(self, item, body):
        return body

    async def delete_item(self, item, partition_key):
        return None


class _DummyTherapistDB:
    async def create_container_if_not_exists(self, **kwargs):
        return _DummyTherapistContainer()


class _DummyTherapistCosmosClient:
    def __init__(self, *args, **kwargs): pass
    async def close(self): pass
    async def create_database_if_not_exists(self, **kwargs):
        return _DummyTherapistDB()

# ── Blob no-ops ───────────────────────────────────────────────────────────────
async def _fake_blob(therapist_id, patient_id, session_id, filename, data, content_type):
    return f"{therapist_id}/{patient_id}/{session_id}/{filename}"

async def _fake_meta(therapist_id, patient_id, session_id, metadata):
    return f"{therapist_id}/{patient_id}/{session_id}/metadata.json"


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_therapist_register():
    """POST /therapist: registers therapist via Cosmos DB (default path)."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    _DummyTherapistContainer._query_results = []  # no existing therapist → no conflict
    client = TestClient(app)
    payload = {
        "first_name":    "Jane",
        "last_name":     "Doe",
        "email":         "jane@example.com",
        "password":      "secret123",
        "sex":           "Female",
        "gender":        "Woman",
        "date_of_birth": "1985-06-15",
        "license": {
            "type":   "LCSW",
            "state":  "WA",
            "number": "LIC-WA-12345",
        },
    }
    r = client.post("/api/v1/therapist", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "Doe"
    assert data["license"]["type"] == "LCSW"
    assert data["license"]["state"] == "WA"
    assert data["sex"] == "Female"
    assert data["gender"] == "Woman"
    assert data["date_of_birth"] == "1985-06-15"
    assert "password" not in data, "password must never be returned in the response"
    assert "therapist_id" in data


def test_therapist_register_table_storage():
    """POST /therapist: registers therapist via Table Storage (enable_cosmos_db=False)."""
    from app.config import settings as app_settings
    app_settings.enable_cosmos_db = False
    account_mod.TableServiceClient = _DummyTableService
    try:
        client = TestClient(app)
        payload = {
            "first_name":    "Bob",
            "last_name":     "Builder",
            "email":         "bob@example.com",
            "password":      "secret456",
            "license": {
                "type":   "LPC",
                "state":  "CA",
                "number": "LIC-CA-99999",
            },
        }
        r = client.post("/api/v1/therapist", json=payload)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["first_name"] == "Bob"
        assert data["license"]["type"] == "LPC"
    finally:
        app_settings.enable_cosmos_db = True  # restore default


def test_list_providers():
    client = TestClient(app)
    r = client.get("/api/v1/auth/providers")
    assert r.status_code == 200
    providers = [p["provider"] for p in r.json()]
    assert "entra" in providers or "google" in providers


def test_therapist_get():
    """GET /therapist/{id}: fetches therapist from Cosmos DB."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.get(f"/api/v1/therapist/{_FAKE_THERAPIST_ID}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["therapist_id"] == _FAKE_THERAPIST_ID
    assert data["first_name"] == "Jane"
    assert data["license"]["type"] == "LCSW"


def test_therapist_get_not_found():
    """GET /therapist/{id}: returns 404 for unknown therapist."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/therapist/does-not-exist")
    assert r.status_code == 404, r.text


def test_therapist_update():
    """PUT /therapist/{id}: updates allowed profile fields."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.put(
        f"/api/v1/therapist/{_FAKE_THERAPIST_ID}",
        json={"specialization": "DBT", "years_of_experience": 10},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["therapist_id"] == _FAKE_THERAPIST_ID


def test_therapist_delete():
    """DELETE /therapist/{id}: deletes therapist, returns 204."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.delete(f"/api/v1/therapist/{_FAKE_THERAPIST_ID}")
    assert r.status_code == 204, r.text


def test_therapist_list_by_id():
    """GET /therapists?therapist_id=...: returns 1-item list for known therapist."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/therapists", params={"therapist_id": _FAKE_THERAPIST_ID})
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["therapist_id"] == _FAKE_THERAPIST_ID


def test_therapist_list_admin():
    """GET /therapists (admin): returns all therapists when x-user-id=admin."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    _DummyTherapistContainer._query_results = [_FAKE_THERAPIST]
    try:
        client = TestClient(app)
        r = client.get("/api/v1/therapists", headers={"x-user-id": "admin"})
        assert r.status_code == 200, r.text
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 1
    finally:
        _DummyTherapistContainer._query_results = []


def test_therapist_list_forbidden():
    """GET /therapists without therapist_id and non-admin user should return 403."""
    account_mod.CosmosClient = _DummyTherapistCosmosClient
    client = TestClient(app)
    r = client.get("/api/v1/therapists", headers={"x-user-id": "someuser"})
    assert r.status_code == 403, r.text


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
    assert j["patient_id"] == _P_HASH, "auto-derived patient_id must match expected hash"
    assert "patient_first_name" not in j
    assert "patient_last_name" not in j
    assert "id" in j
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
    assert items[0]["id"] == "sess-1"


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
    assert data["id"] == "sess-1"
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



