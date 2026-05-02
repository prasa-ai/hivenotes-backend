import logging
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from azure.data.tables.aio import TableServiceClient
from azure.cosmos import PartitionKey
from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import CosmosClient
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ResourceExistsError

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Enums ─────────────────────────────────────────────────────────────────────

class LicenseType(str, Enum):
    """Recognised therapist licence / credential types."""
    LCSW  = "LCSW"   # Licensed Clinical Social Worker
    LMFT  = "LMFT"   # Licensed Marriage and Family Therapist
    LPC   = "LPC"    # Licensed Professional Counselor
    LPCC  = "LPCC"   # Licensed Professional Clinical Counselor
    LMHC  = "LMHC"  # Licensed Mental Health Counselor
    PHD   = "PhD"    # Doctor of Philosophy (Psychology)
    PSYD  = "PsyD"   # Doctor of Psychology
    MD    = "MD"     # Medical Doctor (Psychiatrist)
    OTHER = "Other"


class TherapistPracticeRole(str, Enum):
    THERAPIST  = "therapist"
    ADMIN      = "admin"
    SUPERVISOR = "supervisor"


class BiologicalSex(str, Enum):
    MALE           = "Male"
    FEMALE         = "Female"
    INTERSEX       = "Intersex"
    PREFER_NOT_SAY = "PreferNotToSay"


class GenderIdentity(str, Enum):
    MAN            = "Man"
    WOMAN          = "Woman"
    NON_BINARY     = "NonBinary"
    GENDERQUEER    = "Genderqueer"
    GENDERFLUID    = "Genderfluid"
    TRANSGENDER    = "Transgender"
    OTHER          = "Other"
    PREFER_NOT_SAY = "PreferNotToSay"


# ── Sub-models ────────────────────────────────────────────────────────────────

class License(BaseModel):
    """Therapist license/credential information."""
    type: LicenseType = Field(..., description="Credential type, e.g. LCSW, LMFT")
    state: str = Field(..., description="State / province that issued the licence, e.g. 'WA'")
    number: str = Field(..., description="State-issued licence number")


class TherapistPracticeCreate(BaseModel):
    """Optional practice association supplied at registration time.

    Creates a row in the therapist_practice mapping table so the same therapist
    can later be associated with additional practices without schema changes.
    """
    practice_id: str = Field(..., description="ID of the practice to join")
    role: TherapistPracticeRole = Field(
        default=TherapistPracticeRole.THERAPIST,
        description="Role within the practice",
    )


# ── Request / response models ─────────────────────────────────────────────────

class TherapistCreate(BaseModel):
    """Fields collected during therapist registration.

    Identity & compliance
    ---------------------
    ``license`` contains the therapist's real-world source of truth for identity,
    consisting of licence type, state, and number. These are required at registration
    to support audit trails, HIPAA compliance, and future licence-verification flows.

    Tenancy
    -------
    Practice membership is managed via a separate mapping table
    (therapist_practice).  Supply ``initial_practice`` to create the
    first mapping row during registration; leave it ``None`` to add
    practice associations later.
    """
    model_config = {
        "json_schema_extra": {
            "example": {
                "reference_id": "TH-4X9KR2",
                "email": "jane.doe@example.com",
                "first_name": "Jane",
                "last_name": "Doe",
                "password": "s3cur3P@ssw0rd",
                "sex": "Female",
                "gender": "Woman",
                "date_of_birth": "1985-06-15",
                "license": {
                    "type": "LCSW",
                    "state": "WA",
                    "number": "LIC-WA-12345"
                },
                "npi_number": "1234567890",
                "years_of_experience": 8,
                "specialization": "CBT, trauma-informed care",
                "initial_practice": {
                    "practice_id": "practice-abc",
                    "role": "therapist",
                },
            }
        }
    }
    # ── Core identity ──────────────────────────────────────────────────────
    reference_id: str | None = Field(default=None, description="Human-readable reference ID, e.g. TH-4X9KR2")
    first_name: str = Field(..., description="Given name")
    last_name:  str = Field(..., description="Family name")
    email: str = Field(..., description="Work email — used as the login identifier")
    password: str | None = Field(default=None, min_length=8, description="Temporary password (MVP only — replace with Entra SSO)")

    # ── Demographics — required ────────────────────────────────────────────
    sex:           BiologicalSex  = Field(..., description="Biological sex")
    gender:        GenderIdentity = Field(..., description="Gender identity")
    date_of_birth: date           = Field(..., description="Date of birth (YYYY-MM-DD)")

    # ── Licensing — required; uniquely identifies the therapist legally ────
    license: License = Field(..., description="License/credential information")

    # ── Optional — useful for billing / future expansion ──────────────────
    npi_number:          str | None = Field(default=None, description="National Provider Identifier (NPI)")
    years_of_experience: int | None = Field(default=None, description="Years of clinical experience")
    specialization:      str | None = Field(default=None, description="Primary modality, e.g. CBT, DBT, trauma")
    profile_picture_url: str | None = Field(default=None, description="URL to the therapist's profile picture")

    # ── Optional initial practice association ─────────────────────────────
    initial_practice: TherapistPracticeCreate | None = Field(
        default=None,
        description="Creates a therapist_practice mapping row at registration time",
    )


class TherapistUpdate(BaseModel):
    """Fields a therapist may update after registration.

    Identity fields (email, licence details) and credentials are intentionally
    excluded — those require a separate verified workflow.
    """
    model_config = {
        "json_schema_extra": {
            "example": {
                "first_name": "Jane",
                "last_name": "Doe-Smith",
                "specialization": "DBT, grief counselling",
                "years_of_experience": 10,
            }
        }
    }
    first_name:          str | None = None
    last_name:           str | None = None
    gender:              GenderIdentity | None = None
    npi_number:          str | None = None
    years_of_experience: int | None = None
    specialization:      str | None = None
    profile_picture_url: str | None = None


class TherapistResponse(BaseModel):
    """Therapist record returned by the API.  Never includes the password."""
    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "a3f8c2d1-7b4e-4f9a-8c2d-1a7b4e4f9a8c",
                "therapist_id": "a3f8c2d1-7b4e-4f9a-8c2d-1a7b4e4f9a8c",
                "reference_id": "TH-4X9KR2",
                "email": "jane.doe@example.com",
                "first_name": "Jane",
                "last_name": "Doe",
                "sex": "Female",
                "gender": "Woman",
                "date_of_birth": "1985-06-15",
                "license": {
                    "type": "LCSW",
                    "state": "WA",
                    "number": "LIC-WA-12345"
                },
                "npi_number": "1234567890",
                "years_of_experience": 8,
                "specialization": "CBT, trauma-informed care",
                "profile_picture_url": None,
                "created_at": "2026-04-19T12:00:00+00:00",
                "updated_at": "2026-04-19T12:00:00+00:00",
            }
        }
    }
    id:           str | None = None
    therapist_id: str
    reference_id: str | None = None
    first_name:   str
    last_name:    str
    email:        str
    sex:           BiologicalSex
    gender:        GenderIdentity
    date_of_birth: date
    license:             License
    npi_number:          str | None = None
    years_of_experience: int | None = None
    specialization:      str | None = None
    profile_picture_url: str | None = None
    created_at:          str
    updated_at:          str


# ── Cosmos DB helpers ─────────────────────────────────────────────────────────

async def _get_therapists_container():
    """Return (CosmosClient, ContainerProxy) for therapists.
    Partition key: /id
    Caller must call await client.close().
    """
    client = CosmosClient(settings.cosmos_endpoint, settings.cosmos_key)
    db = await client.create_database_if_not_exists(id=settings.cosmos_db_name)
    container = await db.create_container_if_not_exists(
        id=settings.cosmos_therapists_container,
        partition_key=PartitionKey(path="/id"),
    )
    return client, container


@router.post(
    "/therapist",
    response_model=TherapistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a therapist account",
)
async def register_therapist(payload: TherapistCreate):
    """Register a therapist and persist a record to Azure Table Storage.

    The therapist's email (lowercased) is used as both PartitionKey and RowKey.
    If ``initial_practice`` is supplied, a separate mapping row is written to the
    same table under PartitionKey ``mapping~{therapist_id}`` so the therapist can
    later be associated with additional practices without schema changes.
    """
    therapist_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # ── Therapist entity ────────────────────────────────────────────────────────────────
    entity = {
        "PartitionKey":        therapist_id,
        "RowKey":              therapist_id,
        "id":                  therapist_id,
        "therapist_id":        therapist_id,
        "reference_id":        payload.reference_id or "",
        "first_name":          payload.first_name,
        "last_name":           payload.last_name,
        "email":               payload.email,
        "sex":                 payload.sex.value,
        "gender":              payload.gender.value,
        "date_of_birth":       payload.date_of_birth.isoformat(),
        "license_type":        payload.license.type.value,
        "license_state":       payload.license.state,
        "license_number":      payload.license.number,
        "npi_number":          payload.npi_number or "",
        "years_of_experience": payload.years_of_experience if payload.years_of_experience is not None else "",
        "specialization":      payload.specialization or "",
        "profile_picture_url": payload.profile_picture_url or "",
        "created_at":          created_at,
        "updated_at":          created_at,
    }

    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            try:
                await table.create_table()
            except Exception:
                pass
            await table.create_entity(entity=entity)

            # ── Optional initial practice mapping ───────────────────────────────────
            if payload.initial_practice:
                mapping_entity = {
                    "PartitionKey": f"mapping~{therapist_id}",
                    "RowKey":       payload.initial_practice.practice_id,
                    "therapist_id": therapist_id,
                    "practice_id":  payload.initial_practice.practice_id,
                    "role":         payload.initial_practice.role.value,
                    "status":       "active",
                    "joined_at":    created_at,
                }
                await table.upsert_entity(entity=mapping_entity)

    except ResourceExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A therapist with this email is already registered.",
        )
    except HttpResponseError as exc:
        logger.error("register_therapist: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to persist therapist. Please try again.",
        )
    except Exception as exc:
        logger.error("register_therapist: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )

    return TherapistResponse(
        id=                    entity["id"],
        therapist_id=          therapist_id,
        reference_id=          payload.reference_id,
        first_name=            payload.first_name,
        last_name=             payload.last_name,
        email=                 payload.email,
        sex=                   payload.sex,
        gender=                payload.gender,
        date_of_birth=         payload.date_of_birth,
        license=               payload.license,
        npi_number=            payload.npi_number,
        years_of_experience=   payload.years_of_experience,
        specialization=        payload.specialization,
        profile_picture_url=   payload.profile_picture_url,
        created_at=            created_at,
        updated_at=            created_at,
    )

@router.get(
    "/therapist/{therapist_id}",
    response_model=TherapistResponse,
    summary="Get therapist details by therapist_id",
)
async def get_therapist(therapist_id: str):
    """Fetch therapist details from Azure Table Storage by therapist_id."""
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            e = await table.get_entity(partition_key=therapist_id, row_key=therapist_id)
            return TherapistResponse(
                id=                    e.get("id") or therapist_id,
                therapist_id=          therapist_id,
                reference_id=          e.get("reference_id") or None,
                first_name=            e["first_name"],
                last_name=             e["last_name"],
                email=                 e["email"],
                sex=                   BiologicalSex(e["sex"]),
                gender=                GenderIdentity(e["gender"]),
                date_of_birth=         e["date_of_birth"],
                license=               License(
                    type=LicenseType(e["license_type"]),
                    state=e["license_state"],
                    number=e["license_number"]
                ),
                npi_number=            e.get("npi_number") or None,
                years_of_experience=   int(e["years_of_experience"]) if e.get("years_of_experience") else None,
                specialization=        e.get("specialization") or None,
                profile_picture_url=   e.get("profile_picture_url") or None,
                created_at=            e["created_at"],
                updated_at=            e["updated_at"],
            )
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Therapist '{therapist_id}' not found.",
        )
    except HttpResponseError as exc:
        logger.error("get_therapist: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch therapist. Please try again.",
        )
    except Exception as exc:
        logger.error("get_therapist: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )
    
@router.put(
    "/therapist/{therapist_id}",
    response_model=TherapistResponse,
    summary="Update therapist profile fields",
)
async def update_therapist(therapist_id: str, payload: TherapistUpdate):
    """Partially update a therapist's profile.

    Only non-identity fields may be changed here.  Email, licence details, and
    credentials require a separate verified workflow and are intentionally excluded.
    """
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            entity = await table.get_entity(partition_key=therapist_id, row_key=therapist_id)

            updates = payload.model_dump(exclude_none=True)
            entity.update(updates)
            entity["updated_at"] = datetime.now(timezone.utc).isoformat()

            await table.update_entity(mode="merge", entity=entity)

            return TherapistResponse(
                id=                    entity.get("id") or therapist_id,
                therapist_id=          therapist_id,
                reference_id=          entity.get("reference_id") or None,
                first_name=            entity["first_name"],
                last_name=             entity["last_name"],
                email=                 entity["email"],
                sex=                   BiologicalSex(entity["sex"]),
                gender=                GenderIdentity(entity["gender"]),
                date_of_birth=         entity["date_of_birth"],
                license=               License(
                    type=LicenseType(entity["license_type"]),
                    state=entity["license_state"],
                    number=entity["license_number"]
                ),
                npi_number=            entity.get("npi_number") or None,
                years_of_experience=   int(entity["years_of_experience"]) if entity.get("years_of_experience") else None,
                specialization=        entity.get("specialization") or None,
                profile_picture_url=   entity.get("profile_picture_url") or None,
                created_at=            entity["created_at"],
                updated_at=            entity["updated_at"],
            )
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Therapist '{therapist_id}' not found.",
        )
    except HttpResponseError as exc:
        logger.error("update_therapist: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to update therapist. Please try again.",
        )
    except Exception as exc:
        logger.error("update_therapist: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )
    
@router.delete(
    "/therapist/{therapist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a therapist account",
)
async def delete_therapist(therapist_id: str):
    """Delete a therapist account from Azure Table Storage."""
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            await table.delete_entity(partition_key=therapist_id, row_key=therapist_id)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Therapist '{therapist_id}' not found.",
        )
    except HttpResponseError as exc:
        logger.error("delete_therapist: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to delete therapist. Please try again.",
        )
    except Exception as exc:
        logger.error("delete_therapist: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )
    
@router.get(
    "/therapists",
    response_model=list[TherapistResponse],
    summary="List therapists (admin can list all)",
)
async def list_therapists(
    request: Request,
    therapist_id: str | None = Query(None, description="Optional therapist ID (email). If omitted, admin can list all therapists."),
):
    """List therapists by therapist_id or list all therapists for admin users.

    - If therapist_id is provided, returns that single therapist (as a 1-item list).
    - If therapist_id is omitted, only admin users can list all therapists via x-user-id header.
    """
    # Try to get user_id from state (set by middleware) or from header
    user_id = (getattr(request.state, "user_id", None) or request.headers.get("x-user-id") or "").strip().lower()
    
    if not therapist_id and not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either ?therapist_id=<id> query parameter or x-user-id header must be provided.",
        )
    
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)

            therapists = []

            if therapist_id:
                tid = therapist_id.lower()
                e = await table.get_entity(partition_key=tid, row_key=tid)
                therapists.append(TherapistResponse(
                    id=                    e.get("id") or tid,
                    therapist_id=          e.get("RowKey", ""),
                    reference_id=          e.get("reference_id") or None,
                    first_name=            e["first_name"],
                    last_name=             e["last_name"],
                    email=                 e["email"],
                    sex=                   BiologicalSex(e["sex"]),
                    gender=                GenderIdentity(e["gender"]),
                    date_of_birth=         e["date_of_birth"],
                    license=               License(
                        type=LicenseType(e["license_type"]),
                        state=e["license_state"],
                        number=e["license_number"]
                    ),
                    npi_number=            e.get("npi_number") or None,
                    years_of_experience=   int(e["years_of_experience"]) if e.get("years_of_experience") else None,
                    specialization=        e.get("specialization") or None,
                    profile_picture_url=   e.get("profile_picture_url") or None,
                    created_at=            e["created_at"],
                    updated_at=            e["updated_at"],
                ))
                return therapists

            if user_id != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only admin can list all therapists.",
                )

            entities = table.list_entities()
            async for e in entities:
                # Skip non-therapist rows (e.g., mapping rows) kept in the same table.
                if e.get("PartitionKey") != e.get("RowKey"):
                    continue
                if "license_number" not in e or "license_type" not in e:
                    continue

                therapists.append(TherapistResponse(
                    id=                    e.get("id") or e.get("RowKey", ""),
                    therapist_id=          e.get("RowKey", ""),
                    reference_id=          e.get("reference_id") or None,
                    first_name=            e["first_name"],
                    last_name=             e["last_name"],
                    email=                 e["email"],
                    sex=                   BiologicalSex(e["sex"]),
                    gender=                GenderIdentity(e["gender"]),
                    date_of_birth=         e["date_of_birth"],
                    license=               License(
                        type=LicenseType(e["license_type"]),
                        state=e["license_state"],
                        number=e["license_number"]
                    ),
                    npi_number=            e.get("npi_number") or None,
                    years_of_experience=   int(e["years_of_experience"]) if e.get("years_of_experience") else None,
                    specialization=        e.get("specialization") or None,
                    profile_picture_url=   e.get("profile_picture_url") or None,
                    created_at=            e["created_at"],
                    updated_at=            e["updated_at"],
                ))
            return therapists
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Therapist '{therapist_id}' not found.",
        )
    except HttpResponseError as exc:
        logger.error("list_therapists: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to list therapists. Please try again.",
        )
    except Exception as exc:
        logger.error("list_therapists: Unexpected error — %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )