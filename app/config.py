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

    # Notification placeholders (populate to enable)
    # Email — Azure Communication Services, SendGrid, or SMTP
    admin_email: str = "admin@testhivenotes.com"
    azure_email_connection_string: str = ""
    # Slack — Incoming Webhook or Bot Token
    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""

    # App
    max_upload_size_mb: int = 50

    # LangGraph checkpointing — set to true to enable Azure Table Storage persistence
    enable_checkpoint: bool = False


settings = Settings()
