"""Helpers for resolving SQLite SQLAlchemy URLs to filesystem paths.

Kept separate from ``config`` and ``migrations`` so both can import these
helpers without a circular dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

_SQLITE_PREFIX = "sqlite:///"
_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")


def anchor_sqlite_url(url: str, base_dir: Path) -> str:
    """Rewrite a relative ``sqlite:///...`` URL to an absolute one under ``base_dir``.

    Absolute sqlite URLs (POSIX ``sqlite:////...`` or Windows drive-letter
    ``sqlite:///C:/...``) and non-file URLs (in-memory, or any non-sqlite
    dialect) are returned unchanged. This is what guarantees the app,
    Alembic, tests, and scripts all resolve the exact same physical database
    file no matter which directory the process was started from.
    """
    if not url.startswith(_SQLITE_PREFIX):
        return url
    raw_path = url[len(_SQLITE_PREFIX) :]
    if raw_path in ("", ":memory:"):
        return url
    if raw_path.startswith("/") or _WINDOWS_ABS_RE.match(raw_path):
        return url
    resolved = (base_dir / raw_path).resolve()
    return f"{_SQLITE_PREFIX}{resolved.as_posix()}"


def sqlite_file_path(url: str) -> Path | None:
    """Return the filesystem path backing a file-based sqlite URL, else ``None``."""
    if not url.startswith(_SQLITE_PREFIX):
        return None
    raw_path = url[len(_SQLITE_PREFIX) :]
    if raw_path in ("", ":memory:"):
        return None
    return Path(raw_path)
