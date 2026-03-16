from __future__ import annotations

"""
LangGraph node — Step 4: Transcribe audio and produce a clean transcript.

Transcription backend (toggled via USE_WHISPER_TRANSCRIPTION env var):
───────────────────────────────────────────────────────────────────────
  False (default) → gpt-4o-mini
      The audio bytes are sent directly to a GPT-4o-mini deployment using a
      system prompt that instructs the model to both transcribe and clean the
      text in a single call (lower latency, lower cost).

  True            → Azure AI Whisper
      The audio bytes are sent to a Whisper deployment for verbatim
      transcription (step 4a), and the raw output is then cleaned by a
      separate GPT call (step 4b).

Future rate-limiting upgrade
────────────────────────────
  Replace _select_backend() with logic that counts requests within the current
  minute (e.g. using a Redis counter or an in-process sliding-window counter).
  When the count reaches 4 or above, return True to route to Whisper.
  Everything else in this file stays unchanged.

Reads:   state["audio_bytes"], state["original_filename"]
Writes:  state["raw_transcript"]  — verbatim source text (audit trail)
         state["transcript_text"] — cleaned, filler-free text
"""
import io
import logging
from openai import AzureOpenAI, AsyncAzureOpenAI
from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)

# # ── Shared cleaning prompt (used by both backends' GPT cleaning pass) ─────────
# _FILLER_EXAMPLES = (
#     "um, uh, umm, uhh, hmm, er, ah, like, you know, you know what I mean, "
#     "so, right, okay, well, actually, basically, literally, I mean, sort of, "
#     "kind of, just, anyway"
# )

# _CLEANING_SYSTEM_PROMPT = (
#     "You are a medical transcription editor. "
#     "You will receive a raw, verbatim transcript of a therapy session. "
#     "Your task is to produce a clean, readable version of the text by:\n"
#     f"  1. Removing all spoken fillers such as: {_FILLER_EXAMPLES}.\n"
#     "  2. Removing false-starts, repeated words, and self-corrections "
#     "     (keep only the final intended wording).\n"
#     "  3. Correcting punctuation and capitalisation.\n"
#     "  4. Preserving all clinical content, patient statements, and "
#     "     therapist observations exactly — do NOT paraphrase or summarise.\n"
#     "Return ONLY the cleaned transcript text, with no commentary or headings."
# )

# # gpt-4o-mini system prompt: single-pass transcription + cleaning
# _MINI_TRANSCRIPTION_PROMPT = (
#     "You are a medical transcription assistant. "
#     "You will receive an audio file of a therapy session. "
#     "Transcribe the audio accurately, then immediately apply the following "
#     "cleaning rules to the output:\n"
#     f"  1. Remove all spoken fillers such as: {_FILLER_EXAMPLES}.\n"
#     "  2. Remove false-starts, repeated words, and self-corrections.\n"
#     "  3. Correct punctuation and capitalisation.\n"
#     "  4. Preserve all clinical content verbatim — do NOT paraphrase.\n"
#     "Return ONLY the cleaned transcript, no commentary or headings."
# )

def _select_backend() -> bool:
    """
    Return True to use Whisper, False to use gpt-4o-mini.

    Current logic: reads USE_WHISPER_TRANSCRIPTION from environment config.

    Future upgrade — replace the body of this function with a sliding-window
    rate counter, e.g.:
        count = await rate_counter.increment(window_seconds=60)
        return count >= 4
    No other code in this file needs to change.
    """
    return settings.use_whisper_transcription


# ── Node ──────────────────────────────────────────────────────────────────────

async def transcribe_node(state: GraphState) -> GraphState:
    """
    Transcribe the audio using the selected backend and return a cleaned
    transcript ready for SOAP note generation.
    """
    audio_bytes: bytes = state.get("audio_bytes", b"")
    original_filename: str = state.get("original_filename", "audio.wav")

    if not audio_bytes:
        msg = "transcribe: audio_bytes is empty — store_audio node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    use_whisper = _select_backend()
    logger.info(
        "transcribe: backend selected → %s",
        "Whisper" if use_whisper else "gpt-4o-mini",
    )

    if use_whisper:
        logger.info("azure_openai_api_key=%s azure_openai_endpoint=%s", settings.azure_openai_api_key, settings.azure_openai_endpoint)
        client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version="2024-06-01",
        )
        raw_transcript, cleaned_transcript = await _transcribe_with_whisper(
            client, audio_bytes, original_filename
        )
    else:
        logger.info("azure_gpt_mini_transcribe_api_key=%s azure_gpt_mini_transcribe_endpoint=%s", settings.azure_gpt_mini_transcribe_api_key, settings.azure_gpt_mini_transcribe_endpoint)
        client = AzureOpenAI(
            azure_endpoint=settings.azure_gpt_mini_transcribe_endpoint,
            api_key=settings.azure_gpt_mini_transcribe_api_key,
            api_version="2025-03-01-preview"
        )
        raw_transcript, cleaned_transcript = _transcribe_with_gpt_mini(
            client, audio_bytes, original_filename
        )

    if raw_transcript is None or cleaned_transcript is None:
        # Error already logged inside the helper; propagate sentinel
        return {**state, "error": "transcribe: transcription failed — see logs for details."}

    return {
        **state,
        "raw_transcript":  raw_transcript,
        "transcript_text": cleaned_transcript,
        "error":           None,
    }


# ── Backend implementations ───────────────────────────────────────────────────

async def _transcribe_with_whisper(
    client: AsyncAzureOpenAI,
    audio_bytes: bytes,
    original_filename: str,
) -> tuple[str | None, str | None]:
    """
     Use Whisper for verbatim transcription
    """
    # Pass 1: Whisper
    logger.info(
        "transcribe[whisper]: sending %d bytes to deployment '%s'",
        len(audio_bytes), settings.azure_whisper_deployment,
    )
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = original_filename
        transcription = await client.audio.transcriptions.create(
            model=settings.azure_whisper_deployment,
            file=audio_file,
            response_format="text",
        )
        raw_transcript: str = (
            transcription if isinstance(transcription, str) else transcription.text
        )
        logger.info("transcribe[whisper]: got %d chars", len(raw_transcript))
    except Exception as exc:
        logger.error("transcribe[whisper]: Whisper API error — %s", exc)
        return None, None

    if not raw_transcript.strip():
        logger.warning("transcribe[whisper]: Whisper returned empty transcript")
        return None, None
    
    cleaned = raw_transcript
    return raw_transcript, cleaned


def _transcribe_with_gpt_mini(
    client: AzureOpenAI,
    audio_bytes: bytes,
    original_filename: str,
) -> tuple[str | None, str | None]:
    """
    Single-pass pipeline using gpt-4o-mini's native audio input:
      The model transcribes and cleans in one call.
      raw_transcript == cleaned_transcript (no separate verbatim pass).
    """
    logger.info(
        "transcribe[gpt-4o-mini]: sending %d bytes to deployment '%s'",
        len(audio_bytes), settings.azure_gpt_mini_transcribe_deployment,
    )
    try:
        import base64
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = original_filename

        transcript_response = client.audio.transcriptions.create(
            model=settings.azure_gpt_mini_transcribe_deployment,
            file=audio_file,
            response_format="text",
        )
        transcript: str = (
            transcript_response
            if isinstance(transcript_response, str)
            else transcript_response.text
        )
        logger.info("transcribe[gpt-4o-mini]: got %d chars", len(transcript))
    except Exception as exc:
        logger.error("transcribe[gpt-4o-mini]: API error — %s", exc)
        return None, None

    if not transcript:
        logger.warning("transcribe[gpt-4o-mini]: returned empty transcript")
        return None, None

    # Both raw and cleaned are the same for the mini single-pass path
    return transcript, transcript
