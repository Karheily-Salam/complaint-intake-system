"""Builds the configured :class:`EmailProvider`."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.email.base import EmailProvider


@lru_cache
def get_email_provider() -> EmailProvider:
    provider = settings.email_provider.lower()

    if provider == "mock":
        from app.email.providers.mock import MockEmailProvider

        return MockEmailProvider(support_address=settings.support_inbox_address)

    raise ValueError(
        f"Unknown EMAIL_PROVIDER '{settings.email_provider}'. Only 'mock' is implemented."
    )
