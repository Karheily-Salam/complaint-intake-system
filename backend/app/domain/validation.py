"""Schema-driven field validation.

Rules come entirely from :class:`FieldSpec` / :class:`FieldValidation`, so adding
a complaint type or method never touches this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.complaint_schemas.spec import FieldSpec, FieldType

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class FieldValidationResult:
    key: str
    ok: bool
    normalized_value: str | None = None
    error: str | None = None


def validate_field(spec: FieldSpec, raw_value: str | None) -> FieldValidationResult:
    if raw_value is None or not str(raw_value).strip():
        if spec.required:
            return FieldValidationResult(spec.key, ok=False, error="Value is required.")
        return FieldValidationResult(spec.key, ok=True, normalized_value=None)

    value = str(raw_value).strip()
    v = spec.validation

    if v.min_length is not None and len(value) < v.min_length:
        return FieldValidationResult(
            spec.key, False, error=f"Must be at least {v.min_length} characters."
        )
    if v.max_length is not None and len(value) > v.max_length:
        return FieldValidationResult(
            spec.key, False, error=f"Must be at most {v.max_length} characters."
        )

    if spec.type == FieldType.EMAIL and not _EMAIL_RE.match(value):
        return FieldValidationResult(spec.key, False, error="Not a valid email address.")

    if spec.type == FieldType.NUMBER:
        try:
            float(value.replace(",", ""))
        except ValueError:
            return FieldValidationResult(spec.key, False, error="Not a valid number.")

    if spec.type == FieldType.ENUM and v.enum_values and value not in v.enum_values:
        allowed = ", ".join(v.enum_values)
        return FieldValidationResult(spec.key, False, error=f"Must be one of: {allowed}.")

    if spec.type == FieldType.DATE:
        formats = v.date_formats or ["%Y-%m-%d"]
        parsed = _parse_date(value, formats)
        if parsed is None:
            return FieldValidationResult(spec.key, False, error="Not a recognisable date.")
        return FieldValidationResult(spec.key, True, normalized_value=parsed.strftime("%Y-%m-%d"))

    if v.pattern and not re.fullmatch(v.pattern, value):
        return FieldValidationResult(spec.key, False, error="Value has an unexpected format.")

    return FieldValidationResult(spec.key, ok=True, normalized_value=value)


def validate_fields(
    specs: list[FieldSpec], values: dict[str, str | None]
) -> list[FieldValidationResult]:
    return [validate_field(spec, values.get(spec.key)) for spec in specs]


def _parse_date(value: str, formats: list[str]) -> datetime | None:
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
