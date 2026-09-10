from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    environment: str
    ai_provider: str
    ai_provider_available: bool = True
    email_provider: str
    # False when the configured email provider is missing required settings.
    # The names of the missing variables are logged at startup, never returned
    # here - this endpoint is reachable through the public Nginx proxy.
    email_provider_configured: bool = True
    complaint_types: list[str]
