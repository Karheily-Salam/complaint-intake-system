from __future__ import annotations

from fastapi import APIRouter

from app.ai.factory import get_ai_provider
from app.core.config import settings
from app.domain.complaint_schemas.registry import get_registry
from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    provider = get_ai_provider()
    return HealthResponse(
        app=settings.app_name,
        environment=settings.environment,
        ai_provider=settings.ai_provider,
        ai_provider_available=await provider.available(),
        email_provider=settings.email_provider,
        email_provider_configured=not settings.missing_email_settings(),
        complaint_types=get_registry().types(),
    )
