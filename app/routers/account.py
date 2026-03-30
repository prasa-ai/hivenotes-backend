import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from azure.data.tables.aio import TableServiceClient
from azure.core.exceptions import HttpResponseError

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Therapist account management endpoints. This will include 
# POST for creating new therapist accounts, 
# GET for fetching therapist details by therapist_id, etc.
# GET for listing therapists in a practice (for admin users)
# PUT for updating therapist details, etc.
# DELETE for removing therapist accounts, etc.

class Address(BaseModel):
    street: str = Field(..., description="Street address")
    city: str = Field(..., description="City")
    state: str = Field(..., description="State or province")
    postal_code: str = Field(..., description="Postal or ZIP code")
    country: str = Field(..., description="Country")

class Practice(BaseModel):
    practice_id: str = Field(..., description="Practice identifier")
    name: str = Field(..., description="Practice name")
    address: Address = Field(..., description="Practice address")
    contact_info: dict = Field(default_factory=dict, description="Practice contact information such as phone number, email, etc.")
    
class TherapistCreate(BaseModel):
    first_name: str = Field(...)
    last_name: str = Field(...)
    practice_id: str = Field(..., description="Practice identifier")
    email: str = Field(...)
    location: str = Field(...)
    password: str = Field(..., min_length=8, description="Password for the therapist account")  # In a real app, you'd want to handle passwords securely (hashing, salting, etc.) and not store them in plaintext. This is just for demonstration.
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat()) 
    profile_picture_url: str | None = Field(default=None, description="URL to the therapist's profile picture")
    # practice details which is an array of practice objects including practice id, name, address, contact info, etc. This allows a therapist to be associated with multiple practices if needed. For simplicity, we can start with just practice_id and expand later as needed.
    practice: list[Practice] = Field(default_factory=list, description="List of practices the therapist is associated with")

class TherapistResponse(TherapistCreate):
    therapist_id: str
    created_at: str


@router.post(
    "/therapist",
    response_model=TherapistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a therapist account",
)
async def register_therapist(payload: TherapistCreate):
    """Register a therapist and persist a record to Azure Table Storage.

    PartitionKey will be the `practice_id` and RowKey a generated `therapist_id`.
    """
    # Use the email id as the therapist_id for simplicity, but in a real application, you'd likely want a more robust approach to generating unique identifiers.
    therapist_id = payload.email.lower()
    created_at = datetime.now(timezone.utc).isoformat()
    entity = {
        "PartitionKey": therapist_id,
        "RowKey": therapist_id,
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "email": payload.email,
        "location": payload.location,
        "created_at": created_at,
        "updated_at": created_at,
        "profile_picture_url": payload.profile_picture_url,
        "practice": [practice.dict() for practice in payload.practice],
    }

    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            try:
                await table.create_table()
            except Exception:
                pass
            await table.create_entity(entity=entity)

    except HttpResponseError as exc:
        logger.error("register_therapist: Table Storage error — %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to persist therapist. Please try again.",
        )

    return TherapistResponse(
        therapist_id=therapist_id,
        created_at=created_at,
        updated_at=created_at,
        **payload.model_dump(exclude={"created_at", "updated_at"}),
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
            entity = await table.get_entity(partition_key=therapist_id, row_key=therapist_id)
            return TherapistResponse(
                therapist_id=entity["therapist_id"],
                first_name=entity["first_name"],
                last_name=entity["last_name"],
                email=entity["email"],
                location=entity["location"],
                created_at=entity["created_at"],
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
    summary="Update therapist details",
)
async def update_therapist(therapist_id: str, payload: TherapistCreate):
    """Update therapist details in Azure Table Storage. Only certain fields can be updated."""
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            entity = await table.get_entity(partition_key=therapist_id, row_key=therapist_id)

            # Update only allowed fields
            entity["first_name"] = payload.first_name
            entity["last_name"] = payload.last_name
            entity["email"] = payload.email
            entity["location"] = payload.location
            entity["updated_at"] = datetime.now(timezone.utc).isoformat()

            await table.update_entity(mode="replace", entity=entity)

            return TherapistResponse(
                therapist_id=entity["therapist_id"],
                first_name=entity["first_name"],
                last_name=entity["last_name"],
                email=entity["email"],
                location=entity["location"],
                created_at=entity["created_at"],
                updated_at=entity["updated_at"],
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
    summary="List therapists in a practice",
)
async def list_therapists(practice_id: str):
    """List all therapists associated with a given practice_id."""
    try:
        async with TableServiceClient.from_connection_string(settings.azure_table_connection_string) as service:
            table = service.get_table_client(settings.azure_table_name)
            entities = table.query_entities(f"PartitionKey eq '{practice_id}'")
            therapists = []
            async for entity in entities:
                therapists.append(TherapistResponse(
                    therapist_id=entity["therapist_id"],
                    first_name=entity["first_name"],
                    last_name=entity["last_name"],
                    email=entity["email"],
                    location=entity["location"],
                    created_at=entity["created_at"],
                    updated_at=entity["updated_at"],
                ))
            return therapists
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