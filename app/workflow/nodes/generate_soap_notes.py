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
from openai import AzureOpenAI
from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
_SOAP_SYSTEM_PROMPT = """\
You are an expert medical documentation assistant specialising in therapy \
session notes. You will receive a cleaned transcript of a therapy session \
between a therapist and a patient."""

_SOAP_USER_PROMPT = """\
Your task is to produce a structured SOAP note in **valid JSON** using exactly \
the following four keys and using this transcript recorded by therapist 
{transcript_text}

  "subjective"  — The patient's own words: reported symptoms, feelings, \
concerns, history, and relevant personal context as expressed during the session.

  "objective"   — The clinician's factual observations: behaviour, affect, \
speech, cognition, any reported measurements or test results, and observable \
changes since the last session.

  "assessment"  — The clinician's professional interpretation: diagnosis or \
diagnostic impressions, risk assessment, progress against treatment goals, and \
clinical reasoning.

  "plan"        — Concrete next steps: interventions agreed upon, medications, \
homework tasks, referrals, next appointment, and any safety planning.

Rules:
- Return ONLY a JSON object with these four keys. No markdown fences, no \
preamble, no commentary.
- Write in clear, professional clinical language.
- If a section cannot be determined from the transcript, set its value to \
"Not documented in this session."
- Do NOT invent clinical details not present in the transcript.
"""


# ── Node ──────────────────────────────────────────────────────────────────────

def generate_soap_node(state: GraphState) -> GraphState:
    """
    Call Azure OpenAI GPT to turn the cleaned transcript into a structured
    SOAP note, then validate and parse the JSON response.
    """
    transcript_text: str = state.get("transcript_text", "")

    if not transcript_text:
        msg = "generate_soap: transcript_text is empty — store_transcript node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    logger.info(
        "generate_soap: sending %d-char transcript to GPT deployment '%s'  endpoint=%s",
        len(transcript_text),
        settings.azure_soap_deployment,
        settings.azure_soap_endpoint,
    )

    client = AzureOpenAI(
        api_key=settings.azure_soap_api_key,
        azure_endpoint=settings.azure_soap_endpoint,
        api_version=settings.azure_soap_api_version,
    )

    try:
        response = client.chat.completions.create(
            model=settings.azure_soap_deployment,
            messages=[
                {"role": "system", "content": _SOAP_SYSTEM_PROMPT},
                {"role": "user",   "content": _SOAP_USER_PROMPT.format(transcript_text=transcript_text)},
            ],
            temperature=0.2,       # low temperature for consistent clinical output
            max_tokens=2048,
            response_format={"type": "json_object"},   # force JSON mode
        )
    except Exception as exc:
        msg = f"generate_soap: Azure OpenAI API error — {exc}"
        logger.error(msg)
        return {**state, "error": msg}

    soap_text: str = response.choices[0].message.content.strip()
    logger.info("generate_soap: received %d-char response", len(soap_text))

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
