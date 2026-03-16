"""
LangGraph node — Step 5: Store the cleaned transcript in Azure Blob Storage.

Writes two files to the same container and base folder used in Step 3:

  {blob_folder_path}/raw_transcript.txt     — verbatim Whisper output (audit trail)
  {blob_folder_path}/transcript.txt         — cleaned, filler-free text (GPT input)

e.g.
  therapist1/client1/session1/raw_transcript.txt
  therapist1/client1/session1/transcript.txt

Reads:   state["blob_folder_path"], state["raw_transcript"], state["transcript_text"]
Writes:  state["transcript_blob_path"]   — path to the cleaned transcript blob
"""
import logging
from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.storage.blob import ContentSettings
from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)

# _RAW_FILENAME = "raw_transcript.txt"
_CLEAN_FILENAME = "transcript.txt"
_CONTENT_TYPE = "text/plain; charset=utf-8"


async def store_transcript_node(state: GraphState) -> GraphState:
    """
    Upload both the raw and cleaned transcripts to Azure Blob Storage under
    the session folder resolved in Step 2.

    The cleaned transcript path is stored in state for use by the SOAP generation node.
    """
    blob_folder_path: str = state.get("blob_folder_path", "")
    # raw_transcript: str = state.get("raw_transcript", "")
    transcript_text: str = state.get("transcript_text", "")

    if not blob_folder_path:
        msg = "store_transcript: blob_folder_path is empty — fetch_folder node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    if not transcript_text:
        msg = "store_transcript: transcript_text is empty — transcribe node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    # raw_blob_path   = f"{blob_folder_path}/{_RAW_FILENAME}"
    clean_blob_path = f"{blob_folder_path}/{_CLEAN_FILENAME}"

    logger.info(
        "store_transcript: uploading transcripts to container='%s'  folder='%s/%s'",
        settings.azure_blob_container_name,
        settings.azure_blob_container_name,
        blob_folder_path
    )

    content_settings = ContentSettings(content_type=_CONTENT_TYPE)

    try:
        async with BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        ) as service:
            container = service.get_container_client(settings.azure_blob_container_name)

            # Container is created in store_audio_node, but guard here for resilience
            try:
                await container.create_container()
            except ResourceExistsError:
                pass

            # Upload raw verbatim transcript (audit trail)
            # if raw_transcript:
            #     await container.get_blob_client(raw_blob_path).upload_blob(
            #         raw_transcript.encode("utf-8"),
            #         blob_type="BlockBlob",
            #         content_settings=content_settings,
            #         overwrite=True,
            #     )
            #     logger.info("store_transcript: raw transcript uploaded → %s", raw_blob_path)

            # Upload cleaned transcript (used by GPT in Step 6)
            await container.get_blob_client(clean_blob_path).upload_blob(
                transcript_text.encode("utf-8"),
                blob_type="BlockBlob",
                content_settings=content_settings,
                overwrite=True,
            )
            logger.info("store_transcript: clean transcript uploaded → %s", clean_blob_path)

    except HttpResponseError as exc:
        msg = f"store_transcript: Azure Blob Storage error — {exc.message}"
        logger.error(msg)
        return {**state, "error": msg}

    return {
        **state,
        "transcript_blob_path": clean_blob_path,
        "error": None,
    }
