"""
LangGraph node — Step 8: Store the generated DOCX in Azure Blob Storage and
trigger post-save notifications.

Blob path
─────────
  {therapist_id}/{client_id}/{session_id}/SOAP.docx

  e.g. therapist1/client1/session1/SOAP.docx

Note: The blob path is constructed directly from the identity fields rather
than from blob_folder_path so the DOCX always lands at a predictable,
human-readable location regardless of any intermediate path transformations.

Post-save actions (placeholders — replace with real implementations)
────────────────────────────────────────────────────────────────────
  1. Email notification → admin@testhivenotes.com
  2. Slack channel notification

Reads:   state["docx_bytes"], state["therapist_id"], state["client_id"],
         state["session_id"], state["job_id"]
Writes:  state["docx_blob_path"]
"""
import logging
from datetime import datetime, timezone

from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.storage.blob import ContentSettings

from app.config import settings
from app.workflow.state import GraphState

logger = logging.getLogger(__name__)

_DOCX_FILENAME    = "SOAP.docx"
_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_ADMIN_EMAIL = "admin@testhivenotes.com"


# ── Node ──────────────────────────────────────────────────────────────────────

async def store_docx_node(state: GraphState) -> GraphState:
    """
    Upload the DOCX bytes to Azure Blob Storage, log a structured success
    message, and dispatch admin email + Slack notifications.
    """
    docx_bytes: bytes  = state.get("docx_bytes", b"")
    therapist_id: str  = state.get("therapist_id", "unknown")
    client_id: str     = state.get("client_id", "unknown")
    session_id: str    = state.get("session_id", "unknown")
    job_id: str        = state.get("job_id", "unknown")

    if not docx_bytes:
        msg = "store_docx: docx_bytes is empty — generate_docx node may have failed."
        logger.error(msg)
        return {**state, "error": msg}

    docx_blob_path = f"{therapist_id}/{client_id}/{session_id}/{_DOCX_FILENAME}"
    completed_at   = datetime.now(timezone.utc).isoformat()

    logger.info(
        "store_docx: uploading DOCX | container=%s  path=%s  size=%d bytes",
        settings.azure_blob_container_name,
        docx_blob_path,
        len(docx_bytes),
    )

    # ── Upload to Azure Blob Storage ──────────────────────────────────────────
    try:
        async with BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        ) as service:
            container = service.get_container_client(settings.azure_blob_container_name)

            try:
                await container.create_container()
            except ResourceExistsError:
                pass  # container already exists

            await container.get_blob_client(docx_blob_path).upload_blob(
                docx_bytes,
                blob_type="BlockBlob",
                content_settings=ContentSettings(content_type=_DOCX_CONTENT_TYPE),
                overwrite=True,
            )

    except HttpResponseError as exc:
        msg = f"store_docx: Azure Blob Storage error — {exc.message}"
        logger.error(msg)
        return {**state, "error": msg}

    # ── Structured success log ────────────────────────────────────────────────
    logger.info(
        "store_docx: SUCCESS | "
        "job_id=%s  therapist=%s  client=%s  session=%s  "
        "blob=%s  size_bytes=%d  container=%s  completed_at=%s",
        job_id,
        therapist_id,
        client_id,
        session_id,
        docx_blob_path,
        len(docx_bytes),
        settings.azure_blob_container_name,
        completed_at,
    )

    # ── Post-save notifications ───────────────────────────────────────────────
    await _notify_admin_email(
        job_id=job_id,
        therapist_id=therapist_id,
        client_id=client_id,
        session_id=session_id,
        docx_blob_path=docx_blob_path,
        completed_at=completed_at,
    )

    await _notify_slack(
        job_id=job_id,
        therapist_id=therapist_id,
        client_id=client_id,
        session_id=session_id,
        docx_blob_path=docx_blob_path,
        completed_at=completed_at,
    )

    return {
        **state,
        "docx_blob_path": docx_blob_path,
        "error":          None,
    }


# ── Notification placeholders ─────────────────────────────────────────────────

async def _notify_admin_email(
    job_id: str,
    therapist_id: str,
    client_id: str,
    session_id: str,
    docx_blob_path: str,
    completed_at: str,
) -> None:
    """
    PLACEHOLDER — Send a completion email to the administrator.

    Replace this body with a real email implementation, e.g.:
      • Azure Communication Services Email SDK
          from azure.communication.email.aio import EmailClient
          client = EmailClient.from_connection_string(settings.azure_email_connection_string)
          await client.begin_send(message)

      • SendGrid
          import sendgrid
          sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
          sg.client.mail.send.post(request_body=mail.get())

      • SMTP via aiosmtplib
          await aiosmtplib.send(message, hostname=settings.smtp_host, ...)
    """
    subject = f"[SOAP Notes] DOCX ready — {therapist_id} / {client_id} / {session_id}"
    body = (
        f"A new SOAP note document has been generated and stored.\n\n"
        f"  Job ID      : {job_id}\n"
        f"  Therapist   : {therapist_id}\n"
        f"  Client      : {client_id}\n"
        f"  Session     : {session_id}\n"
        f"  Blob path   : {docx_blob_path}\n"
        f"  Completed at: {completed_at}\n"
    )
    logger.info(
        "store_docx[email]: PLACEHOLDER — would send email to %s | subject='%s'",
        _ADMIN_EMAIL,
        subject,
    )
    logger.debug("store_docx[email]: body=\n%s", body)
    # TODO: implement email delivery using the provider of your choice (see docstring above)


async def _notify_slack(
    job_id: str,
    therapist_id: str,
    client_id: str,
    session_id: str,
    docx_blob_path: str,
    completed_at: str,
) -> None:
    """
    PLACEHOLDER — Post a notification to a configured Slack channel.

    Replace this body with a real Slack implementation, e.g.:
      • Slack Incoming Webhook (simplest)
          async with httpx.AsyncClient() as client:
              await client.post(
                  settings.slack_webhook_url,
                  json={"text": message, "blocks": [...]},
              )

      • slack-sdk (bolt / WebClient)
          from slack_sdk.web.async_client import AsyncWebClient
          slack = AsyncWebClient(token=settings.slack_bot_token)
          await slack.chat_postMessage(channel=settings.slack_channel_id, text=message)
    """
    message = (
        f":white_check_mark: *SOAP Note ready*\n"
        f">*Job*: `{job_id}`\n"
        f">*Therapist*: `{therapist_id}`  |  *Client*: `{client_id}`  "
        f"|  *Session*: `{session_id}`\n"
        f">*Blob*: `{docx_blob_path}`\n"
        f">*Completed*: {completed_at}"
    )
    logger.info(
        "store_docx[slack]: PLACEHOLDER — would post to Slack channel | message=%r",
        message,
    )
    # TODO: implement Slack delivery using the provider of your choice (see docstring above)
