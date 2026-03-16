"""
LangGraph node — Step 2: Fetch (or auto-create) the blob folder path from Azure Table Storage.

Table schema
────────────
  PartitionKey : therapist_id          (e.g. "therapist-123")
  RowKey       : client_id             (e.g. "client-123")
  blob_root    : str                   (e.g. "therapist-123/client-123")

The node builds the full folder path as:
  {blob_root}/{session_id}
→  "therapist-123/client-123/session-123"

This path is used by all subsequent nodes to read/write to Blob Storage.

Auto-creation
─────────────
If no record exists for (therapist_id, client_id), one is created automatically
using blob_root = "{therapist_id}/{client_id}" so that the first upload for any
new therapist/client pair works without manual Table Storage setup.
"""
import logging
from azure.data.tables.aio import TableServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, HttpResponseError
from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)


async def fetch_folder_node(state: GraphState) -> GraphState:
    """
    Query Azure Table Storage for the blob root that corresponds to
    (therapist_id, client_id).  If no record exists, one is created
    automatically with blob_root = "{therapist_id}/{client_id}".

    Populates: state["blob_folder_path"]
    """
    therapist_id = state["therapist_id"]
    client_id = state["client_id"]
    session_id = state["session_id"]

    logger.info(
        "fetch_folder: querying Table Storage for therapist=%s client=%s",
        therapist_id,
        client_id,
    )

    try:
        async with TableServiceClient.from_connection_string(
            settings.azure_table_connection_string
        ) as service:
            # Ensure the table itself exists (no-op if it already does)
            try:
                await service.create_table(settings.azure_table_name)
                logger.info("fetch_folder: created table '%s'", settings.azure_table_name)
            except ResourceExistsError:
                pass

            table_client = service.get_table_client(settings.azure_table_name)

            try:
                entity = await table_client.get_entity(
                    partition_key=therapist_id,
                    row_key=client_id,
                )
                blob_root: str = entity.get("blob_root", "").strip("/")
                logger.info(
                    "fetch_folder: found existing mapping  blob_root=%s", blob_root
                )

            except ResourceNotFoundError:
                # Auto-create the mapping using the standard folder pattern
                blob_root = f"{therapist_id}/{client_id}"
                new_entity = {
                    "PartitionKey": therapist_id,
                    "RowKey": client_id,
                    "blob_root": blob_root,
                }
                await table_client.create_entity(new_entity)
                logger.info(
                    "fetch_folder: created new mapping  therapist=%s client=%s  blob_root=%s",
                    therapist_id,
                    client_id,
                    blob_root,
                )

    except HttpResponseError as exc:
        msg = f"Azure Table Storage error: {exc.message}"
        logger.error(msg)
        return {**state, "error": msg}

    if not blob_root:
        msg = (
            f"Table entity for therapist='{therapist_id}', client='{client_id}' "
            f"is missing the 'blob_root' column."
        )
        logger.error(msg)
        return {**state, "error": msg}

    blob_folder_path = f"{blob_root}/{session_id}"
    logger.info("fetch_folder: resolved folder path → %s", blob_folder_path)

    return {**state, "blob_folder_path": blob_folder_path, "error": None}
