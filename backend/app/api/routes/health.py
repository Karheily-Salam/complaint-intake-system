from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.domain.complaint_schemas.registry import get_registry
from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        app=settings.app_name,
        environment=settings.environment,
        ai_provider=settings.ai_provider,
        email_provider=settings.email_provider,
        complaint_types=get_registry().types(),
    )
