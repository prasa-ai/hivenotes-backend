"""
LangGraph checkpoint persistence backed by Azure Table Storage + Azure Blob Storage.

Design
──────
  Azure Table Storage  →  lightweight index (metadata, ordering, blob-path references)
  Azure Blob Storage   →  serialised state payloads

Both services reuse the connection strings already configured for the SOAP Notes
workflow — no new Azure infrastructure or additional packages are required.

Single-table approach
─────────────────────
Checkpoint data lives in the SAME table as UserSessions (AZURE_SESSIONS_TABLE_NAME)
using prefixed PartitionKeys so checkpoint rows are completely isolated from session rows:

  UserSessions  (AZURE_SESSIONS_TABLE_NAME — table already exists, no new table created)

  Checkpoint index rows:
    PartitionKey        – "lgcp~{thread_id}"
    RowKey              – "{checkpoint_ns}~{checkpoint_id}"
    checkpoint_id       – str
    checkpoint_ns       – str
    parent_checkpoint_id– str  (empty string = no parent)
    ts                  – ISO-8601 UTC timestamp  (used for "latest" queries)
    blob_path           – path to the serialised Checkpoint blob
    metadata_b64        – base64-encoded, serde-serialised CheckpointMetadata

  Pending-write rows:
    PartitionKey        – "lgwr~{thread_id}~{checkpoint_ns}~{checkpoint_id}"
    RowKey              – "{task_id}~{idx:06d}"
    task_id             – str
    channel             – str
    blob_path           – path to the serialised write-value blob

Why not Table Storage alone for payloads?
─────────────────────────────────────────
Azure Table Storage caps each entity at 1 MB and each string property at 64 KB.
The SOAP Notes GraphState keeps `audio_bytes` (up to 50 MB) in state until the
transcription node consumes it, so payloads are stored in Blob Storage instead.

Blob layout  (under the existing AZURE_BLOB_CONTAINER_NAME)
────────────
  langgraph/checkpoints/{thread_id}/{checkpoint_id}.bin
  langgraph/writes/{thread_id}/{checkpoint_id}/{task_id}_{idx}.bin

  Each .bin file: b"{serde_type_tag}\\x00{serialised_bytes}"

Fallback
────────
  If Azure connection strings are absent (e.g. local dev without a .env file)
  the module falls back to LangGraph's built-in MemorySaver so the app still
  starts — state just won't survive container restarts.
"""

import base64
import datetime
import logging
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables.aio import TableClient as AsyncTableClient
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.config import settings

logger = logging.getLogger(__name__)

_checkpointer: Optional["AzureTableCheckpointer"] = None


# ── Checkpointer class ────────────────────────────────────────────────────────

class AzureTableCheckpointer(BaseCheckpointSaver):
    """
    LangGraph checkpoint saver that stores the checkpoint index inside the
    existing UserSessions table and state payloads in Azure Blob Storage.

    Checkpoint rows use PartitionKey prefix "lgcp~" and write rows use
    prefix "lgwr~" so they are fully isolated from session entities.
    """

    # PartitionKey prefixes — must not overlap with UserSessions partition keys
    _CP_PREFIX = "lgcp~"
    _WR_PREFIX = "lgwr~"

    def __init__(
        self,
        table_client: AsyncTableClient,
        blob_service: AsyncBlobServiceClient,
        blob_container: str,
        blob_prefix: str = "langgraph",
    ) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._table = table_client
        self._blobs = blob_service
        self._container = blob_container
        self._prefix = blob_prefix

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Ensure the blob container exists.  The table already exists (UserSessions)."""
        try:
            await self._blobs.create_container(self._container)
        except ResourceExistsError:
            pass
        logger.info(
            "AzureTableCheckpointer: ready  table=%s  container=%s",
            self._table.table_name,
            self._container,
        )

    async def aclose(self) -> None:
        # table_client is owned by the sessions connection pool; we only close blobs.
        await self._blobs.close()
        logger.info("AzureTableCheckpointer: blob connection closed.")

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _config_values(config: RunnableConfig) -> tuple[str, str, Optional[str]]:
        conf = config.get("configurable", {})
        return (
            conf["thread_id"],
            conf.get("checkpoint_ns", ""),
            conf.get("checkpoint_id"),
        )

    def _cp_pk(self, thread_id: str) -> str:
        """PartitionKey for checkpoint index rows."""
        return f"{self._CP_PREFIX}{thread_id}"

    def _wr_pk(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        """PartitionKey for pending-write rows."""
        return f"{self._WR_PREFIX}{thread_id}~{checkpoint_ns}~{checkpoint_id}"

    @staticmethod
    def _row_key(checkpoint_ns: str, checkpoint_id: str) -> str:
        # ~ is safe in Azure Table RowKey and sorts after alphanumeric chars
        return f"{checkpoint_ns}~{checkpoint_id}"

    def _cp_blob_path(self, thread_id: str, cp_id: str) -> str:
        return f"{self._prefix}/checkpoints/{thread_id}/{cp_id}.bin"

    def _wr_blob_path(self, thread_id: str, cp_id: str, task_id: str, idx: int) -> str:
        return f"{self._prefix}/writes/{thread_id}/{cp_id}/{task_id}_{idx}.bin"

    def _pack(self, obj: Any) -> bytes:
        """Serialise an object to bytes: b'{type_tag}\\x00{data}'"""
        type_tag, data = self.serde.dumps_typed(obj)
        return type_tag.encode() + b"\x00" + data

    def _unpack(self, raw: bytes) -> Any:
        """Deserialise bytes produced by _pack."""
        type_tag, data = raw.split(b"\x00", 1)
        return self.serde.loads_typed((type_tag.decode(), data))

    async def _upload(self, path: str, data: bytes) -> None:
        blob = self._blobs.get_blob_client(container=self._container, blob=path)
        await blob.upload_blob(data, overwrite=True)

    async def _download(self, path: str) -> bytes:
        blob = self._blobs.get_blob_client(container=self._container, blob=path)
        stream = await blob.download_blob()
        return await stream.readall()

    async def _entity_to_tuple(
        self,
        entity: dict,
        thread_id: str,
        checkpoint_ns: str,
        include_writes: bool = False,
    ) -> CheckpointTuple:
        """Convert a Table Storage entity dict to a CheckpointTuple."""
        cp_id: str = entity["checkpoint_id"]

        checkpoint: Checkpoint = self._unpack(await self._download(entity["blob_path"]))

        metadata: CheckpointMetadata = (
            self._unpack(base64.b64decode(entity["metadata_b64"]))
            if entity.get("metadata_b64")
            else {}
        )

        parent_cp_id: str = entity.get("parent_checkpoint_id", "")
        parent_config: Optional[RunnableConfig] = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_cp_id,
                }
            }
            if parent_cp_id
            else None
        )

        pending_writes: Optional[list[tuple[str, str, Any]]] = None
        if include_writes:
            wr_pk = self._wr_pk(thread_id, checkpoint_ns, cp_id)
            writes: list[tuple[str, str, Any]] = []
            async for wr in self._table.query_entities(
                query_filter=f"PartitionKey eq '{wr_pk}'"
            ):
                value = self._unpack(await self._download(wr["blob_path"]))
                writes.append((wr["task_id"], wr["channel"], value))
            pending_writes = writes or None

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": cp_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    # ── Async interface (used by graph.ainvoke) ───────────────────────────────

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id, checkpoint_ns, checkpoint_id = self._config_values(config)
        cp_pk = self._cp_pk(thread_id)

        if checkpoint_id:
            try:
                entity = await self._table.get_entity(
                    partition_key=cp_pk,
                    row_key=self._row_key(checkpoint_ns, checkpoint_id),
                )
            except ResourceNotFoundError:
                return None
        else:
            # Find the most-recent checkpoint for this thread by timestamp
            latest_entity: Optional[dict] = None
            latest_ts = ""
            async for e in self._table.query_entities(
                query_filter=f"PartitionKey eq '{cp_pk}'"
            ):
                ts = e.get("ts", "")
                if ts > latest_ts:
                    latest_ts = ts
                    latest_entity = e
            if latest_entity is None:
                return None
            entity = latest_entity

        return await self._entity_to_tuple(entity, thread_id, checkpoint_ns, include_writes=True)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            return
        thread_id, checkpoint_ns, _ = self._config_values(config)
        cp_pk = self._cp_pk(thread_id)

        # Collect all matching entities, then sort newest-first in Python.
        # Checkpoint history per workflow run is at most ~8 entries, so this is fine.
        before_ts: Optional[str] = None
        if before:
            _, _, before_cp_id = self._config_values(before)
            if before_cp_id:
                try:
                    before_entity = await self._table.get_entity(
                        partition_key=cp_pk,
                        row_key=self._row_key(checkpoint_ns, before_cp_id),
                    )
                    before_ts = before_entity.get("ts", "")
                except ResourceNotFoundError:
                    pass

        entities: list[dict] = []
        async for entity in self._table.query_entities(
            query_filter=f"PartitionKey eq '{cp_pk}'"
        ):
            if before_ts and entity.get("ts", "") >= before_ts:
                continue
            entities.append(entity)

        entities.sort(key=lambda e: e.get("ts", ""), reverse=True)
        if limit is not None:
            entities = entities[:limit]

        for entity in entities:
            # Pending writes omitted for list — they're only needed on resume
            yield await self._entity_to_tuple(entity, thread_id, checkpoint_ns, include_writes=False)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        thread_id, checkpoint_ns, _ = self._config_values(config)
        cp_id: str = checkpoint["id"]
        parent_cp_id: str = config["configurable"].get("checkpoint_id", "")

        # Upload serialised state to Blob Storage
        blob_path = self._cp_blob_path(thread_id, cp_id)
        await self._upload(blob_path, self._pack(checkpoint))

        # Write the index entry to Table Storage
        # metadata is always small so it's stored inline as base64
        entity = {
            "PartitionKey": self._cp_pk(thread_id),
            "RowKey": self._row_key(checkpoint_ns, cp_id),
            "checkpoint_id": cp_id,
            "checkpoint_ns": checkpoint_ns,
            "parent_checkpoint_id": parent_cp_id,
            "ts": datetime.datetime.utcnow().isoformat(),
            "blob_path": blob_path,
            "metadata_b64": base64.b64encode(self._pack(metadata)).decode(),
        }
        cp_table = self._table
        await cp_table.upsert_entity(entity)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": cp_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None:
        thread_id, checkpoint_ns, checkpoint_id = self._config_values(config)
        if not checkpoint_id:
            return
        for idx, (channel, value) in enumerate(writes):
            blob_path = self._wr_blob_path(thread_id, checkpoint_id, task_id, idx)
            await self._upload(blob_path, self._pack(value))
            entity = {
                "PartitionKey": self._wr_pk(thread_id, checkpoint_ns, checkpoint_id),
                "RowKey": f"{task_id}~{idx:06d}",
                "task_id": task_id,
                "channel": channel,
                "blob_path": blob_path,
            }
            await self._table.upsert_entity(entity)

    # ── Sync interface (required by BaseCheckpointSaver; not used with ainvoke) ─────

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:  # type: ignore[override]
        raise NotImplementedError("AzureTableCheckpointer only supports async (use ainvoke).")

    def list(self, config, **kwargs) -> Iterator[CheckpointTuple]:  # type: ignore[override]
        raise NotImplementedError("AzureTableCheckpointer only supports async (use ainvoke).")

    def put(self, config, checkpoint, metadata, new_versions) -> RunnableConfig:  # type: ignore[override]
        raise NotImplementedError("AzureTableCheckpointer only supports async (use ainvoke).")

    def put_writes(self, config, writes, task_id) -> None:  # type: ignore[override]
        raise NotImplementedError("AzureTableCheckpointer only supports async (use ainvoke).")


# ── Module-level lifecycle helpers called from FastAPI lifespan ───────────────

async def init_checkpointer() -> BaseCheckpointSaver:
    """
    Instantiate and return the checkpointer.  Called once at app startup
    from the FastAPI lifespan handler in main.py.

    Falls back to the built-in MemorySaver when ENABLE_CHECKPOINT=false (default)
    or when Azure connection strings are absent.
    """
    global _checkpointer

    if not settings.enable_checkpoint:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info(
            "checkpointer: ENABLE_CHECKPOINT=false — using in-memory MemorySaver. "
            "State will not survive restarts. Set ENABLE_CHECKPOINT=true to enable persistence."
        )
        _checkpointer = MemorySaver()
        return _checkpointer

    if not settings.azure_sessions_table_connection_string or not settings.azure_storage_connection_string:
        from langgraph.checkpoint.memory import MemorySaver
        logger.warning(
            "checkpointer: Azure connection strings not set — "
            "using in-memory MemorySaver.  State will NOT survive restarts."
        )
        _checkpointer = MemorySaver()
        return _checkpointer

    table_client = AsyncTableClient.from_connection_string(
        settings.azure_sessions_table_connection_string,
        table_name=settings.azure_sessions_table_name,
    )
    blob_service = AsyncBlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    )
    saver = AzureTableCheckpointer(
        table_client=table_client,
        blob_service=blob_service,
        blob_container=settings.azure_blob_container_name,
    )
    await saver.setup()
    _checkpointer = saver
    return _checkpointer


async def close_checkpointer() -> None:
    """Gracefully close Azure connections.  Called once at app shutdown."""
    global _checkpointer
    if _checkpointer is not None and hasattr(_checkpointer, "aclose"):
        await _checkpointer.aclose()
    _checkpointer = None


def get_checkpointer() -> Optional[BaseCheckpointSaver]:
    """Return the active checkpointer instance (called by graph.py)."""
    return _checkpointer
