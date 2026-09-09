from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    environment: str
    ai_provider: str
    email_provider: str
    complaint_types: list[str]
