import logging
import json
from typing import Any
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings
from app.config import settings

logger = logging.getLogger(__name__)


async def upload_session_blob(
    therapist_id: str,
    patient_id: str,
    session_id: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a session file to Azure Blob Storage using a therapist/patient/session prefix.

    Returns the blob path used within the container.
    """
    container_name = settings.azure_blob_container_name
    blob_path = f"{therapist_id}/{patient_id}/{session_id}/{filename}"

    logger.info("Uploading blob %s to container %s", blob_path, container_name)

    async with BlobServiceClient.from_connection_string(settings.azure_storage_connection_string) as service:
        container_client = service.get_container_client(container_name)
        try:
            await container_client.create_container()
        except Exception:
            pass

        await container_client.upload_blob(
            name=blob_path,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    return blob_path


async def upload_session_metadata(
    therapist_id: str,
    patient_id: str,
    session_id: str,
    metadata: Any,
) -> str:
    """Upload a small JSON metadata blob next to the session assets."""
    data = json.dumps(metadata, default=str).encode("utf-8")
    return await upload_session_blob(therapist_id, patient_id, session_id, "metadata.json", data, "application/json")
