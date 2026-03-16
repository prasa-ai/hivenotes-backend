"""
LangGraph workflow graph builder and runner.

Nodes are imported from app/workflow/nodes/ and wired together here.
Each node is added incrementally as development tasks are approved.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph, END
from app.workflow.state import GraphState
from app.workflow.nodes.fetch_folder import fetch_folder_node
from app.workflow.nodes.store_audio import store_audio_node
from app.workflow.nodes.transcribe_audio import transcribe_node
from app.workflow.nodes.store_transcript import store_transcript_node
from app.workflow.nodes.generate_soap_notes import generate_soap_node
from app.workflow.nodes.generate_docx import generate_docx_node
from app.workflow.nodes.store_docx import store_docx_node

logger = logging.getLogger(__name__)

# ── State fields that are too large / sensitive to log verbatim ───────────────
_SKIP_LOG_FIELDS = {"audio_bytes", "docx_bytes"}

# Fields to log a size summary for instead of the raw value
_SIZE_LOG_FIELDS = {"raw_transcript", "transcript_text", "soap_text"}


def _summarise_state(state: GraphState) -> dict:
    """Return a log-safe snapshot of the state — large blobs shown as sizes."""
    summary = {}
    for key, value in state.items():
        if key in _SKIP_LOG_FIELDS:
            summary[key] = f"<{len(value)} bytes>" if isinstance(value, (bytes, bytearray)) else "<present>"
        elif key in _SIZE_LOG_FIELDS and isinstance(value, str):
            summary[key] = f"<{len(value)} chars>"
        else:
            summary[key] = value
    return summary


def _traced(name: str, fn: Callable) -> Callable:
    """Wrap a node function with enter/exit/error state logging."""
    async def wrapper(state: GraphState) -> GraphState:
        job_id = state.get("job_id", "?")
        logger.info(
            "[%s] ▶ ENTER node=%s  state=%s",
            job_id, name, _summarise_state(state),
        )
        t0 = time.perf_counter()
        result: GraphState = await fn(state)
        elapsed = time.perf_counter() - t0

        if result.get("error"):
            logger.error(
                "[%s] ✖ ERROR node=%s  elapsed=%.2fs  error=%s",
                job_id, name, elapsed, result["error"],
            )
        else:
            logger.info(
                "[%s] ✔ EXIT  node=%s  elapsed=%.2fs  new_keys=%s",
                job_id, name, elapsed,
                sorted(set(result.keys()) - set(state.keys())),
            )
        return result
    wrapper.__name__ = fn.__name__
    return wrapper


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(GraphState)

    # Step 2 — fetch blob folder path from Azure Table Storage
    graph.add_node("fetch_folder",     _traced("fetch_folder",     fetch_folder_node))
    # Step 3 — upload audio bytes to Azure Blob Storage
    graph.add_node("store_audio",      _traced("store_audio",      store_audio_node))
    # Step 4 — transcribe with Azure Whisper + clean with Azure OpenAI GPT
    graph.add_node("transcribe",        _traced("transcribe",        transcribe_node))
    # Step 5 — store raw + cleaned transcripts in Azure Blob Storage
    graph.add_node("store_transcript",  _traced("store_transcript",  store_transcript_node))
    # Step 6 — generate structured SOAP note via Azure OpenAI GPT
    graph.add_node("generate_soap",     _traced("generate_soap",     generate_soap_node))
    # Step 7 — generate Microsoft DOCX from SOAP sections
    graph.add_node("generate_docx",     _traced("generate_docx",     generate_docx_node))
    # Step 8 — store DOCX in Azure Blob Storage + dispatch notifications
    graph.add_node("store_docx",        _traced("store_docx",        store_docx_node))

    graph.set_entry_point("fetch_folder")
    graph.add_edge("fetch_folder",     "store_audio")
    graph.add_edge("store_audio",      "transcribe")
    graph.add_edge("transcribe",       "store_transcript")
    graph.add_edge("store_transcript", "generate_soap")
    graph.add_edge("generate_soap",    "generate_docx")
    graph.add_edge("generate_docx",    "store_docx")
    graph.add_edge("store_docx",       END)

    return graph


# Compiled lazily at app startup once the checkpointer is ready.
# Call compile_graph(checkpointer) from the FastAPI lifespan handler.
_compiled_graph = None


def compile_graph(checkpointer: BaseCheckpointSaver | None = None) -> None:
    """
    Compile the LangGraph workflow and attach the checkpointer.
    Called once from main.py's lifespan startup handler after init_checkpointer().
    """
    global _compiled_graph
    _compiled_graph = build_graph().compile(checkpointer=checkpointer)


async def run_workflow(
    job_id: str,
    therapist_id: str,
    client_id: str,
    session_id: str,
    audio_bytes: bytes,
    original_filename: str,
    content_type: str,
    job_store: dict,
) -> None:
    """
    Execute the LangGraph workflow as a FastAPI background task.
    Updates `job_store` with status and result at each step.
    """
    job_store[job_id]["status"] = "running"
    job_store[job_id]["step"] = "fetch_folder → store_audio → transcribe → store_transcript → generate_soap → generate_docx → store_docx"

    logger.info(
        "[%s] ▶ WORKFLOW START  therapist=%s  client=%s  session=%s  file=%s  size=%d bytes",
        job_id, therapist_id, client_id, session_id, original_filename, len(audio_bytes),
    )
    t_start = time.perf_counter()

    initial_state: GraphState = {
        "job_id": job_id,
        "therapist_id": therapist_id,
        "client_id": client_id,
        "session_id": session_id,
        "audio_bytes": audio_bytes,
        "original_filename": original_filename,
        "content_type": content_type,
        "error": None,
    }

    # thread_id scopes the checkpoint to this specific job so concurrent runs
    # never share state.  LangGraph resumes from the last saved node if the
    # container restarts mid-run and the job is retried with the same job_id.
    langgraph_config = {"configurable": {"thread_id": job_id}}

    try:
        final_state: GraphState = await _compiled_graph.ainvoke(
            initial_state, config=langgraph_config
        )
        elapsed_total = time.perf_counter() - t_start

        if final_state.get("error"):
            logger.error(
                "[%s] ✖ WORKFLOW FAILED  elapsed=%.2fs  error=%s",
                job_id, elapsed_total, final_state["error"],
            )
            job_store[job_id]["status"] = "failed"
            job_store[job_id]["error"] = final_state["error"]
        else:
            logger.info(
                "[%s] ✔ WORKFLOW COMPLETE  elapsed=%.2fs  docx=%s",
                job_id, elapsed_total, final_state.get("docx_blob_path"),
            )
            job_store[job_id]["status"] = "completed"
            job_store[job_id]["docx_blob_path"] = final_state.get("docx_blob_path")

    except Exception as exc:  # noqa: BLE001
        job_store[job_id]["status"] = "failed"
        job_store[job_id]["error"] = str(exc)
