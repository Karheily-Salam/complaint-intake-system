"""Loads and indexes complaint schema definitions from YAML.

The registry is the single source of truth for required/optional fields. The
conversation engine depends on this abstraction, not on any hard-coded field list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import settings
from app.domain.complaint_schemas.spec import ComplaintSchema


class ComplaintSchemaError(RuntimeError):
    pass


class ComplaintSchemaRegistry:
    def __init__(self, schemas: dict[str, ComplaintSchema]) -> None:
        self._schemas = schemas

    @classmethod
    def from_directory(cls, directory: Path) -> ComplaintSchemaRegistry:
        if not directory.is_dir():
            raise ComplaintSchemaError(f"Schema directory not found: {directory}")

        schemas: dict[str, ComplaintSchema] = {}
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            try:
                schema = ComplaintSchema.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 - surface file context
                raise ComplaintSchemaError(f"Invalid schema in {path.name}: {exc}") from exc
            if schema.type in schemas:
                raise ComplaintSchemaError(f"Duplicate complaint type '{schema.type}'")
            schemas[schema.type] = schema

        if not schemas:
            raise ComplaintSchemaError(f"No schema definitions found in {directory}")
        return cls(schemas)

    # ---- lookup API ----

    def types(self) -> list[str]:
        return list(self._schemas)

    def all(self) -> list[ComplaintSchema]:
        return list(self._schemas.values())

    def get(self, complaint_type: str) -> ComplaintSchema:
        try:
            return self._schemas[complaint_type]
        except KeyError as exc:
            raise ComplaintSchemaError(f"Unknown complaint type '{complaint_type}'") from exc

    def try_get(self, complaint_type: str | None) -> ComplaintSchema | None:
        if complaint_type is None:
            return None
        return self._schemas.get(complaint_type)


@lru_cache
def get_registry() -> ComplaintSchemaRegistry:
    return ComplaintSchemaRegistry.from_directory(settings.complaint_schema_dir)
