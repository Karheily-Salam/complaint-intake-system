"""Minimal logging setup. Structured logging can be layered on later."""

from __future__ import annotations

import logging

from app.core.config import settings


def configure_logging() -> None:
    level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    # Pin the application's own level explicitly instead of inheriting it from
    # root. Alembic's fileConfig() reconfigures the root logger from
    # alembic.ini (which sets it to WARN) when migrations run on startup, and
    # every app logger would otherwise silently inherit that for the rest of
    # the process - hiding, for example, everything the email poller reports.
    logging.getLogger("app").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
