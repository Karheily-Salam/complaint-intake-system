"""Deterministic, offline AI provider.

Uses keyword heuristics and regular expressions - no model, no network. It is the
default provider so the prototype runs anywhere, and it doubles as a stable
baseline for tests. Accuracy is intentionally modest; swap in ``OllamaAIProvider``
for real language understanding.
"""

from __future__ import annotations

import re

from app.ai.base import (
    AIProvider,
    Classification,
    ExtractedField,
    ExtractionResult,
    ReplyDraft,
    TypeOption,
)
from app.domain.complaint_schemas.spec import FieldSpec, FieldType

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "withdrawal": ("withdraw", "withdrawal", "payout", "cash out", "cashout", "take out money"),
    "deposit": ("deposit", "top up", "top-up", "fund my account", "add funds", "transfer in"),
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_USER_ID_RE = re.compile(
    r"(?:user|account|customer|uid)\s*(?:id|number|no\.?|#)?\s*[:#]?\s*([A-Za-z0-9\-]{2,64})",
    re.IGNORECASE,
)
_TXN_RE = re.compile(
    r"(?:transaction|txn|tx|reference|ref|order)\s*(?:id|number|no\.?|#)?\s*[:#]?\s*([A-Za-z0-9\-]{4,128})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\b"
)


class RuleBasedAIProvider(AIProvider):
    name = "rule_based"

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        text = message.lower()
        scores: dict[str, int] = {}
        for opt in options:
            kws = _KEYWORDS.get(opt.type, ())
            scores[opt.type] = sum(1 for kw in kws if kw in text)

        best_type = max(scores, key=scores.get) if scores else None
        best_score = scores.get(best_type, 0) if best_type else 0

        if best_score == 0:
            fallback = next((o.type for o in options if o.type == "other"), None)
            return Classification(
                type=fallback,
                confidence=0.3,
                rationale="No keyword match; defaulting to 'other'.",
            )

        confidence = min(0.5 + 0.2 * best_score, 0.95)
        return Classification(
            type=best_type,
            confidence=confidence,
            rationale=f"Matched {best_score} keyword(s) for '{best_type}'.",
        )

    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
    ) -> ExtractionResult:
        known = known or {}
        found: list[ExtractedField] = []

        for spec in specs:
            if known.get(spec.key):
                continue
            value = self._extract_one(spec, message)
            if value:
                found.append(ExtractedField(key=spec.key, value=value, confidence=0.6))

        return ExtractionResult(fields=found)

    async def summarize(self, transcript: list[str]) -> str:
        customer_lines = [line.strip() for line in transcript if line.strip()]
        if not customer_lines:
            return ""
        joined = " ".join(customer_lines)
        joined = re.sub(r"\s+", " ", joined)
        return joined[:497] + "..." if len(joined) > 500 else joined

    async def compose_reply(
        self,
        missing: list[FieldSpec],
        *,
        complaint_label: str,
        customer_name: str | None = None,
        extra_context: str = "",
    ) -> ReplyDraft:
        greeting = f"Hi {customer_name}," if customer_name else "Hello,"
        if not missing:
            body = (
                f"{greeting}\n\nThank you - we now have everything we need about your "
                f"{complaint_label.lower()} and have created a ticket for our team.\n"
            )
            return ReplyDraft(body=body)

        bullets = "\n".join(f"  - {spec.label}: {spec.description}".rstrip() for spec in missing)
        body = (
            f"{greeting}\n\nThanks for getting in touch about your {complaint_label.lower()}. "
            "To move this forward, could you please provide:\n\n"
            f"{bullets}\n\n"
            "You can reply in your own words - no need for a form.\n"
        )
        if extra_context:
            body += f"\n{extra_context}\n"
        return ReplyDraft(body=body)

    # ---- helpers ----

    def _extract_one(self, spec: FieldSpec, message: str) -> str | None:
        if spec.type == FieldType.EMAIL:
            m = _EMAIL_RE.search(message)
            return m.group(0) if m else None

        if spec.type == FieldType.DATE:
            m = _DATE_RE.search(message)
            return m.group(1) if m else None

        if spec.key == "user_id":
            m = _USER_ID_RE.search(message)
            return m.group(1) if m else None

        if "transaction" in spec.key or "txn" in spec.key or spec.key.endswith("_id"):
            m = _TXN_RE.search(message)
            return m.group(1) if m else None

        if spec.type == FieldType.TEXT:
            # Whole message is the best available description signal.
            cleaned = re.sub(r"\s+", " ", message).strip()
            return cleaned or None

        return None
