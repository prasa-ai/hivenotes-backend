from __future__ import annotations

from typing import Optional, TypedDict


class GraphState(TypedDict, total=False):
    """
    Shared state bag passed between every LangGraph node.
    Fields are populated progressively as each node executes.
    """

    # ── Input (populated by the FastAPI endpoint before graph execution) ──────
    job_id: str
    therapist_id: str
    client_id: str
    session_id: str
    audio_bytes: bytes
    original_filename: str
    content_type: str

    # ── Step 2 output: folder path fetched from Azure Table Storage ───────────
    blob_folder_path: str          # e.g. "therapist1/client1/session1"

    # ── Step 3 output: audio blob path in Azure Blob Storage ─────────────────
    audio_blob_path: str           # e.g. "therapist1/client1/session1/audio.wav"

    # ── Step 4 output: Whisper transcription ─────────────────────────────────
    raw_transcript: str            # verbatim Whisper output (audit trail)
    transcript_text: str           # cleaned / filler-free text

    # ── Step 5 output: transcript stored in blob ─────────────────────────────
    transcript_blob_path: str      # e.g. "therapist1/client1/session1/transcript.txt"

    # ── Step 6 output: SOAP note (Azure OpenAI GPT) ───────────────────────────
    soap_text: str             # raw JSON string returned by the model
    soap_sections: dict        # validated dict: {subjective, objective, assessment, plan}

    # ── Step 7 output: generated .docx bytes ─────────────────────────────────
    docx_bytes: bytes

    # ── Step 8 output: DOCX blob path ────────────────────────────────────────
    docx_blob_path: str            # e.g. "therapist1/client1/session1/soap_note.docx"

    # ── Error propagation ─────────────────────────────────────────────────────
    error: Optional[str]
