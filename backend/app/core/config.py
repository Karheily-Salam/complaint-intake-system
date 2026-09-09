"""Application configuration.

All settings come from environment variables (optionally via ``backend/.env``).
No secrets or environment-specific values are hard-coded here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Application ----
    app_name: str = "Complaint Intake System"
    environment: str = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ---- Database ----
    database_url: str = "sqlite:///./complaint_intake.db"
    db_echo: bool = False

    # ---- CORS ----
    backend_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ---- AI provider ----
    ai_provider: str = "rule_based"  # rule_based | ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_seconds: int = 60

    # ---- Email provider ----
    email_provider: str = "mock"  # mock (only mock implemented in the prototype)
    support_inbox_address: str = "complaints@example.com"

    # ---- Ticketing ----
    ticket_reference_prefix: str = "CMP"

    @property
    def complaint_schema_dir(self) -> Path:
        return BACKEND_DIR / "app" / "domain" / "complaint_schemas" / "definitions"

    @property
    def prompt_template_dir(self) -> Path:
        return BACKEND_DIR / "app" / "conversation" / "prompts"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
