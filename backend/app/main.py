"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.migrations import run_migrations
from app.domain.complaint_schemas.registry import get_registry
from app.services.email_poller import EmailPoller

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if settings.run_migrations_on_startup:
        run_migrations()
    # Fail fast if the complaint schema config is invalid.
    registry = get_registry()
    logger.info("Loaded complaint schemas: %s", ", ".join(registry.types()))
    logger.info(
        "AI provider: %s | Email provider: %s", settings.ai_provider, settings.email_provider
    )

    # Fail fast on a half-configured mail provider rather than discovering it
    # on the first customer email. Only variable *names* are ever reported.
    missing = settings.missing_email_settings()
    if missing:
        raise RuntimeError(
            f"EMAIL_PROVIDER={settings.email_provider} is missing required "
            f"configuration: {', '.join(missing)}"
        )

    poller_task: asyncio.Task | None = None
    if settings.email_provider.lower() != "mock":
        poller_task = asyncio.create_task(EmailPoller().run_forever())

    try:
        yield
    finally:
        if poller_task is not None:
            poller_task.cancel()
            with suppress(asyncio.CancelledError):
                await poller_task


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.backend_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.backend_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs", "api": settings.api_v1_prefix}

    return app


app = create_app()
