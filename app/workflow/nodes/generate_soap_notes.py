"""
LangGraph node — Step 6: Generate a SOAP note from the cleaned transcript
using a GPT model deployed on Azure AI Foundry (Azure OpenAI).

The model is prompted to return a strictly structured JSON object with four
top-level keys so the result can be validated in Python before being passed
to the DOCX generator in Step 7:

  {
    "subjective":  "...",   // Patient's reported symptoms, history, feelings
    "objective":   "...",   // Clinician's observations, measurements, test results
    "assessment":  "...",   // Diagnosis / clinical interpretation
    "plan":        "..."    // Treatment plan, follow-up actions, referrals
  }

Reads:   state["transcript_text"]
Writes:  state["soap_text"]      — raw JSON string returned by the model
         state["soap_sections"]  — validated dict with the four SOAP keys
"""
import json
import logging
import re
from openai import AsyncAzureOpenAI
from app.config import settings
from app.workflow.prompts import get_prompt_set
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)

# ── Prompt loading ────────────────────────────────────────────────────────────
# Resolved once at import time from SOAP_PROMPT_VERSION env var (default
# "v2_clinical"). Override by setting the env var or calling
# get_prompt_set(name) directly in evaluation / test code.
_prompt_set = get_prompt_set()
logger.info(
    "generate_soap: using prompt version '%s' (output_format=%s) — %s",
    _prompt_set.name, _prompt_set.output_format, _prompt_set.description,
)

# Token budget: CoT variants need headroom for the reasoning block.
_MAX_TOKENS = 4096 if _prompt_set.output_format == "xml_cot" else 2048

# ── XML CoT extraction helpers ────────────────────────────────────────────────
_SOAP_NOTES_RE = re.compile(
    r"<soap-notes>\s*(.*?)\s*</soap-notes>",
    re.DOTALL | re.IGNORECASE,
)
_REASONING_RE = re.compile(
    r"<reasoning>\s*(.*?)\s*</reasoning>",
    re.DOTALL | re.IGNORECASE,
)


def _extract_cot_json(raw: str) -> tuple[str, str]:
    """
    Parse an ``xml_cot`` model response.

    Returns ``(reasoning_text, soap_json_string)``.
    Raises ``ValueError`` if the ``<soap-notes>`` block is absent.
    """
    reasoning_match = _REASONING_RE.search(raw)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else ""

    notes_match = _SOAP_NOTES_RE.search(raw)
    if not notes_match:
        raise ValueError(
            "xml_cot response did not contain a <soap-notes> block. "
            f"Raw response starts with: {raw[:200]!r}"
        )
    soap_json = notes_match.group(1).strip()
    return reasoning, soap_json


# ── Node ──────────────────────────────────────────────────────────────────────

async def generate_soap_node(state: GraphState) -> GraphState:
    """
    Call Azure OpenAI GPT to turn the cleaned transcript into a structured
    SOAP note, then validate and parse the JSON response.

    Supports two output modes controlled by ``_prompt_set.output_format``:

    ``json``
        Direct JSON-object mode.  The model returns a JSON object and the
        response is validated against the four required SOAP keys.

    ``xml_cot``
        Chain-of-thought mode (e.g. v3_fidelity_cot).  The model reasons
        through the transcript in a ``<reasoning>`` block, then outputs the
        SOAP JSON inside a ``<soap-notes>`` block.  The reasoning is logged
        at DEBUG level and discarded; only the JSON is persisted.
    """
    transcript_text: str = state.get("transcript_text", "")

    if not transcript_text:
        msg = "generate_soap: transcript_text is empty — store_transcript node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    is_cot = _prompt_set.output_format == "xml_cot"

    logger.info(
        "generate_soap: sending %d-char transcript to GPT deployment '%s'  "
        "endpoint=%s  cot=%s",
        len(transcript_text),
        settings.azure_soap_deployment,
        settings.azure_soap_endpoint,
        is_cot,
    )

    client = AsyncAzureOpenAI(
        api_key=settings.azure_soap_api_key,
        azure_endpoint=settings.azure_soap_endpoint,
        api_version=settings.azure_soap_api_version,
    )

    # CoT variants must NOT use json_object mode — the response contains XML tags and reasoning text, not just the JSON. 
    # Non-CoT variants use json_object mode to enforce structured output.
    create_kwargs: dict = dict(
        model=settings.azure_soap_deployment,
        messages=[
            {"role": "system", "content": _prompt_set.system_prompt},
            {"role": "user",   "content": _prompt_set.format_user(transcript_text)},
        ],
        temperature=0.2,
        max_tokens=_MAX_TOKENS,
    )
    if not is_cot:
        create_kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        msg = f"generate_soap: Azure OpenAI API error — {exc}"
        logger.error(msg)
        return {**state, "error": msg}
    finally:
        await client.close()

    raw_response: str = response.choices[0].message.content.strip()
    logger.info("generate_soap: received %d-char response", len(raw_response))

    # ── Extract JSON from response ────────────────────────────────────────────
    if is_cot:
        try:
            reasoning, soap_text = _extract_cot_json(raw_response)
        except ValueError as exc:
            msg = f"generate_soap: failed to extract <soap-notes> from CoT response — {exc}"
            logger.error(msg)
            return {**state, "error": msg}
        if reasoning:
            logger.debug("generate_soap: CoT reasoning:\n%s", reasoning)
    else:
        soap_text = raw_response

    # ── Validate the JSON structure ───────────────────────────────────────────
    required_keys = {"subjective", "objective", "assessment", "plan"}
    try:
        soap_sections: dict = json.loads(soap_text)
    except json.JSONDecodeError as exc:
        msg = f"generate_soap: model returned invalid JSON — {exc}"
        logger.error(msg)
        return {**state, "error": msg}

    missing = required_keys - soap_sections.keys()
    if missing:
        # Fill any missing sections rather than failing the whole pipeline
        logger.warning("generate_soap: missing SOAP keys %s — filling with placeholder", missing)
        for key in missing:
            soap_sections[key] = "Not documented in this session."
        soap_text = json.dumps(soap_sections, ensure_ascii=False, indent=2)

    logger.info("generate_soap: SOAP note generated successfully: \n%s", soap_text)

    return {
        **state,
        "soap_text":     soap_text,
        "soap_sections": soap_sections,
        "error":         None,
    }
