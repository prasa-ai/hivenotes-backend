"""
LangGraph node — Step 3: Store the audio file in Azure Blob Storage.

Uses the blob_folder_path resolved in Step 2 (fetch_folder_node) to construct
the full blob path:

  {blob_folder_path}/{original_filename}
  → "therapist1/client1/session1/audio.wav"

The audio bytes received from the FastAPI upload endpoint (state["audio_bytes"])
are uploaded to the configured Azure Blob Storage container.

Populates: state["audio_blob_path"]
"""
import logging
from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)


async def store_audio_node(state: GraphState) -> GraphState:
    """
    Upload the audio bytes to Azure Blob Storage under the folder path
    that was fetched from Table Storage in the previous node.

    Reads:   state["audio_bytes"], state["blob_folder_path"],
             state["original_filename"], state["content_type"]
    Writes:  state["audio_blob_path"]
    """
    blob_folder_path: str = state.get("blob_folder_path", "")
    audio_bytes: bytes = state.get("audio_bytes", b"")
    original_filename: str = state.get("original_filename", "audio")
    content_type: str = state.get("content_type", "audio/wav")

    if not blob_folder_path:
        msg = "store_audio: blob_folder_path is empty — fetch_folder node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    if not audio_bytes:
        msg = "store_audio: audio_bytes is empty — nothing to upload."
        logger.error(msg)
        return {**state, "error": msg}

    # Sanitise the filename to prevent path traversal in the blob name
    safe_filename = _sanitise_filename(original_filename)
    audio_blob_path = f"{blob_folder_path}/{safe_filename}"

    logger.info(
        "store_audio: uploading %d bytes → container=%s  blob=%s",
        len(audio_bytes),
        settings.azure_blob_container_name,
        audio_blob_path,
    )

    try:
        async with BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        ) as service:
            container_client = service.get_container_client(
                settings.azure_blob_container_name
            )

            # Create the container if it does not exist yet
            try:
                await container_client.create_container()
                logger.info("store_audio: created container '%s'", settings.azure_blob_container_name)
            except ResourceExistsError:
                pass  # container already exists — expected in production

            blob_client = container_client.get_blob_client(audio_blob_path)
            await blob_client.upload_blob(
                audio_bytes,
                blob_type="BlockBlob",
                content_settings=_build_content_settings(content_type),
                overwrite=True,  # idempotent re-upload on retry
            )

    except HttpResponseError as exc:
        msg = f"store_audio: Azure Blob Storage error: {exc.message}"
        logger.error(msg)
        return {**state, "error": msg}

    logger.info("store_audio: upload complete → %s", audio_blob_path)
    return {**state, "audio_blob_path": audio_blob_path, "error": None}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_filename(name: str) -> str:
    """
    Strip directory components and characters that are not safe in a blob name.
    Keeps alphanumerics, hyphens, underscores, and the extension dot.
    """
    import re
    # Take only the last path segment (guards against directory traversal)
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Replace anything that is not word chars, dot or hyphen with underscore
    return re.sub(r"[^\w.\-]", "_", base) or "audio"


def _build_content_settings(content_type: str):
    """Return a ContentSettings object with the correct MIME type."""
    from azure.storage.blob import ContentSettings
    return ContentSettings(content_type=content_type)
