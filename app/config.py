from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Azure Blob Storage
    azure_storage_connection_string: str = ""
    azure_blob_container_name: str = "session-notes"

    # Azure Table Storage (key-value mapping store)
    azure_table_connection_string: str = ""
    azure_table_name: str = "therapist"

    # Azure Table Storage (user sessions)
    azure_sessions_table_connection_string: str = ""
    azure_sessions_table_name: str = "usersession"

    # Azure OpenAI / Whisper + GPT cleaning
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_whisper_deployment: str = "whisper"
    azure_gpt_mini_deployment: str = "gpt-4o-mini" # used for transcript cleaning & SOAP

    azure_gpt_mini_transcribe_endpoint: str = ""
    azure_gpt_mini_transcribe_api_key: str = ""
    azure_gpt_mini_transcribe_deployment: str = "gpt-4o-mini-transcribe"

    # Azure OpenAI endpoint for SOAP generation (gpt-4o-mini chat completions)
    azure_soap_endpoint: str = ""
    azure_soap_api_key: str = ""
    azure_soap_api_version: str = ""
    azure_soap_deployment: str = "gpt-4o-mini"  # deployment name on the SOAP resource

    # Transcription backend toggle
    # False (default) → gpt-4o-mini  |  True → Whisper
    # Future: overridden at runtime by rate-limiting logic (every 4th+ req/min → Whisper)
    use_whisper_transcription: bool = False

    # OAuth / SSO settings (optional) — used to construct login URLs
    azure_ad_client_id: str = ""
    azure_ad_tenant_id: str = ""
    azure_ad_redirect_uri: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Notification placeholders (populate to enable)
    # Email — Azure Communication Services, SendGrid, or SMTP
    admin_email: str = "admin@testhivenotes.com"
    azure_email_connection_string: str = ""
    # Slack — Incoming Webhook or Bot Token
    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""

    # SOAP prompt variant — must match a key in app/workflow/prompts/
    # Available: v1_basic, v2_clinical  (add more by dropping a vN_*.py file)
    soap_prompt_version: str = "v2_clinical"

    # Feature toggles — set to false to bypass external services during local dev / LLM testing
    # ENABLE_BLOB_STORAGE=false  → skip Azure Blob upload (audio + metadata sidecar)
    # ENABLE_COSMOS_DB=false     → skip Cosmos DB write/read (session record persistence)
    # LangGraph workflow always runs regardless of these flags.
    enable_blob_storage: bool = False
    enable_cosmos_db: bool = False

    # App
    max_upload_size_mb: int = 50

    # Azure Cosmos DB (NoSQL) — sessions container
    cosmos_endpoint: str = ""
    cosmos_key: str = ""
    cosmos_db_name: str = "hivenotes"
    cosmos_sessions_container: str = "sessions"

    # LangGraph checkpointing — set to true to enable Azure Table Storage persistence
    enable_checkpoint: bool = False


settings = Settings()
