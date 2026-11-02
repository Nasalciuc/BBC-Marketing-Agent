"""Content system settings — environment variables with the CONTENT_ prefix.

Set them as Railway variables and run commands with `railway run`; never in a committed .env.
"""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

TRANSACTION_POOLER_PORT = 6543


class ContentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    enabled: bool = False          # the webhook hands "cs:" updates to the content system only when true
    db_url: str = ""               # Supabase session pooler (port 5432), role content_app — migrations/011
    db_schema: str = "content"
    db_pool_max: int = 4
    lock_timeout_s: float = 8.0    # how long a tap waits for another tap on the same post (toast deadline)
    output_root: str = "output/content"


@lru_cache(maxsize=1)
def get_settings() -> ContentSettings:
    return ContentSettings()


def db_url_problem(url: str) -> str | None:
    """Why this URL can't hold the checkpoints, or None when it can."""
    if not url:
        return "CONTENT_DB_URL is not set"
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        return "CONTENT_DB_URL must start with postgresql://"
    if parts.port == TRANSACTION_POOLER_PORT:
        return (
            "CONTENT_DB_URL points at the transaction pooler (port 6543). Use the Session pooler "
            "string (port 5432): the checkpointer needs prepared statements and a stable session."
        )
    return None
