"""Application configuration.

All settings come from environment variables (optionally via ``backend/.env``).
No secrets or environment-specific values are hard-coded here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.db_path import anchor_sqlite_url

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
    # Local-prototype convenience: apply pending Alembic migrations automatically
    # when the FastAPI app starts, so a fresh clone works without a separate
    # manual step. This only ever calls `alembic upgrade head` programmatically -
    # Alembic remains the sole schema-authoring mechanism (see app.core.migrations).
    # For a real deployment set this to false and run migrations as an explicit
    # release step instead.
    run_migrations_on_startup: bool = True

    # ---- CORS ----
    backend_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ---- AI provider ----
    ai_provider: str = "rule_based"  # rule_based | ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_seconds: int = 60
    # When AI_PROVIDER=ollama and Ollama is unreachable: if true, transparently
    # fall back to the rule-based provider; if false, raise a clear provider error.
    ollama_fallback_to_rule_based: bool = True

    # ---- Conversation engine ----
    # Below this classifier confidence the engine treats the complaint type as
    # not yet known and asks the customer to clarify.
    min_classification_confidence: float = 0.45

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

    @model_validator(mode="after")
    def _anchor_database_url(self) -> Settings:
        # Runs for every Settings() instance regardless of whether database_url
        # came from the default, backend/.env, or an environment variable - a
        # relative sqlite URL otherwise resolves against the process's current
        # working directory, which is what let the app and Alembic silently
        # point at two different SQLite files.
        self.database_url = anchor_sqlite_url(self.database_url, BACKEND_DIR)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
