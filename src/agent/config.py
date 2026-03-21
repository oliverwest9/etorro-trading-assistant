from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    etoro_api_key: str
    etoro_user_key: str
    etoro_base_url: str = "https://public-api.etoro.com/api/v1"

    surreal_url: str
    surreal_namespace: str
    surreal_database: str
    surreal_user: str
    surreal_pass: str

    llm_provider: str = "gemini"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.0-flash"

    news_api_url: str = ""

    currency_symbol: str = "£"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
