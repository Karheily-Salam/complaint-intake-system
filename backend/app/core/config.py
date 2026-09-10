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
    # Below this language-detection confidence the engine keeps the
    # conversation's previously known language instead of switching to
    # whatever the (unreliable) detection guessed for this message.
    min_language_confidence: float = 0.5

    # ---- Staff API authentication ----
    # Shared secret for the endpoints that expose real customer data
    # (tickets, conversations) and mutate real tickets. Supplied via
    # STAFF_API_KEY; there is deliberately no default, and the staff endpoints
    # fail closed (503) when it is unset rather than serving PII openly.
    staff_api_key: str | None = None

    # ---- Email provider ----
    email_provider: str = "mock"  # mock | imap_smtp
    # Where completed tickets are sent (the support/admin team's own inbox).
    support_inbox_address: str = "complaints@example.com"
    # Domain used when generating our outbound Message-IDs. Only an identifier
    # (it never has to resolve), but using the real sending domain is correct
    # and helps some providers preserve the header rather than rewrite it.
    mail_domain: str = "example.com"

    # ---- Inbound email (IMAP polling, EMAIL_PROVIDER=imap_smtp) ----
    imap_host: str | None = None
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"
    # How often the background poller checks the mailbox.
    email_poll_interval_seconds: int = 60
    # Socket timeout for IMAP. Without one, a connection that is accepted but
    # then blackholed (exactly what a silently-dropping firewall produces)
    # hangs the poller forever.
    imap_timeout_seconds: int = 30
    # Belt-and-braces ceiling applied by the poller around any provider call,
    # in case a provider ignores its own socket timeout.
    email_operation_timeout_seconds: int = 120

    # ---- Outbound email (SMTP, EMAIL_PROVIDER=imap_smtp) ----
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    # smtp_use_ssl=true  -> implicit TLS on connect (typically port 465)
    # smtp_use_tls=true  -> STARTTLS after connecting (typically port 587)
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = 30
    # Defaults to the polled mailbox when unset (see smtp_sender).
    smtp_from_addr: str | None = None

    @property
    def smtp_sender(self) -> str:
        """The address customer-facing mail is sent from.

        Defaults to the *polled* mailbox, not the support inbox: a customer
        replies to whatever the From address is, and those replies have to
        come back to the mailbox the poller reads, or the conversation is lost.
        support_inbox_address is only the last resort, for the mock provider
        where no IMAP mailbox is configured at all.
        """
        return self.smtp_from_addr or self.imap_username or self.support_inbox_address

    def missing_email_settings(self) -> list[str]:
        """Env var names required by the configured email provider but unset.

        Returns names only - never values - so this is safe to log and safe to
        surface in an error message. An empty list means the provider is
        fully configured.
        """
        if self.email_provider.lower() != "imap_smtp":
            return []
        required = {
            "IMAP_HOST": self.imap_host,
            "IMAP_USERNAME": self.imap_username,
            "IMAP_PASSWORD": self.imap_password,
            "SMTP_HOST": self.smtp_host,
            "SMTP_USERNAME": self.smtp_username,
            "SMTP_PASSWORD": self.smtp_password,
        }
        return [name for name, value in required.items() if not value]

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
