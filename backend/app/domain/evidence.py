"""Evidence for extracted values: where in the customer's message each came from.

Every AI provider promises never to invent a value. This module is what
checks that promise instead of trusting it. For each value an extractor
returns, it finds the exact span of the customer's message that supports it,
or reports that nothing does - and the engine then refuses the value
(``EXTRACTION_REQUIRE_EVIDENCE``, on by default).

Matching, from strictest to loosest - the first that succeeds wins:

``exact``
    The value appears in the message, ignoring case and runs of whitespace.
``date``
    For date fields: a written date in the message (numeric, "8 September",
    "вчера", "أمس", ...) resolves to the same calendar day as the value, so
    "2026-09-08" is supported by "8 сентября".
``normalized``
    For short identifier-like values: the same letters and digits appear in
    order, ignoring separators - "U482913" is supported by "U-482913".
``overlap``
    For free-text (``text``) fields only, which may legitimately paraphrase
    (an LLM summarising "what happened"): most of the value's words occur in
    the message. The evidence is the best-matching sentence.

Spans are character offsets into the text the engine saw - the message with
quoted reply history removed - not the raw stored body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.domain.complaint_schemas.spec import FieldSpec, FieldType
from app.domain.dates import date_spans

# Bumped whenever the matching rules change, and recorded with every
# extraction so an evaluation can tell which policy produced a result.
EVIDENCE_POLICY_VERSION = "evidence-v1"

_MAX_EVIDENCE_CHARS = 300
_MIN_OVERLAP = 0.6
_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?؟\n]+[.!?؟]?")


@dataclass(frozen=True)
class Evidence:
    text: str
    start: int
    end: int
    method: str


def locate_evidence(
    spec: FieldSpec,
    value: str | None,
    message: str,
    *,
    normalized_value: str | None = None,
    reference_date: date | None = None,
) -> Evidence | None:
    """The span of ``message`` supporting ``value``, or None if nothing does."""
    value = (value or "").strip()
    if not value or not message:
        return None

    span = _find_flexible(value, message)
    if span:
        return _evidence(message, *span, "exact")

    if spec.type == FieldType.DATE:
        target = normalized_value or value
        formats = spec.validation.date_formats or []
        for start, end, iso in date_spans(message, formats, reference_date):
            if iso == target:
                return _evidence(message, start, end, "date")
        return None

    if spec.type == FieldType.TEXT:
        return _overlap_evidence(value, message)

    span = _find_alnum(value, message)
    if span:
        return _evidence(message, *span, "normalized")
    return None


def _evidence(message: str, start: int, end: int, method: str) -> Evidence:
    end = min(end, start + _MAX_EVIDENCE_CHARS)
    return Evidence(text=message[start:end], start=start, end=end, method=method)


def _find_flexible(value: str, message: str) -> tuple[int, int] | None:
    parts = value.split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(p) for p in parts)
    match = re.search(pattern, message, re.IGNORECASE)
    return (match.start(), match.end()) if match else None


def _find_alnum(value: str, message: str) -> tuple[int, int] | None:
    target = "".join(ch for ch in value.lower() if ch.isalnum())
    if len(target) < 2:
        return None
    positions = [i for i, ch in enumerate(message) if ch.isalnum()]
    stream = "".join(message[i].lower() for i in positions)
    found = stream.find(target)
    if found < 0:
        return None
    return positions[found], positions[found + len(target) - 1] + 1


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _overlap_evidence(value: str, message: str) -> Evidence | None:
    value_words = set(_words(value))
    if len(value_words) < 2:
        return None
    message_words = set(_words(message))
    if len(value_words & message_words) / len(value_words) < _MIN_OVERLAP:
        return None
    best: tuple[int, int, int] | None = None
    for match in _SENTENCE_RE.finditer(message):
        score = len(value_words & set(_words(match.group())))
        if score and (best is None or score > best[0]):
            best = (score, match.start(), match.end())
    if best is None:
        return None
    start, end = best[1], best[2]
    while start < end and message[start].isspace():
        start += 1
    return _evidence(message, start, end, "overlap")
