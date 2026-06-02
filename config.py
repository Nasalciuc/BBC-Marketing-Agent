"""BBC Marketing Agent — Configuration"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Gemini (AI discovery)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Anthropic (Claude — text, captions, AI agent)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    text_llm_provider: str = "claude"  # claude | gemini

    # Unsplash (stock photos)
    unsplash_access_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_storage_bucket: str = "marketing"

    # Google Sheets
    google_sheets_id: str = ""
    google_service_account_json: str = ""

    # Twilio (WhatsApp broadcast)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_content_sid: str = ""

    # Telegram Bot
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_admin_ids: str = ""

    # WAHA (WhatsApp group posting)
    waha_url: str = "http://localhost:3000"
    waha_api_key: str = ""
    whatsapp_group_id: str = ""

    # Email backup
    resend_api_key: str = ""
    email_from: str = ""
    email_to: str = ""

    # Prefect
    prefect_api_key: str = ""

    # BBC Branding
    bbc_navy: str = "#0B1829"
    bbc_gold: str = "#C9A54E"
    branding_renderer: str = "auto"  # auto | playwright | hcti
    hcti_user_id: str = ""
    hcti_api_key: str = ""

    # Environment
    debug: bool = False
    environment: str = "development"

    @property
    def admin_ids(self) -> set[int]:
        """Admin user IDs — din telegram_admin_ids sau fallback telegram_chat_id."""
        raw = self.telegram_admin_ids or self.telegram_chat_id
        return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
