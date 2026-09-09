"""Deterministic, offline AI provider.

Uses keyword heuristics and regular expressions - no model, no network. It is the
default provider so the prototype runs anywhere, and it is the stable baseline the
automated tests rely on. Accuracy is intentionally modest; swap in
``OllamaAIProvider`` for real natural-language understanding.

It never invents data: every returned value is copied verbatim from the customer's
message (only whitespace-trimmed).
"""

from __future__ import annotations

import re

from app.ai.base import (
    AIProvider,
    Classification,
    ExtractedField,
    ExtractionResult,
    ReplyDraft,
    ReplyKind,
    ReplyRequest,
    TypeOption,
)
from app.domain.complaint_schemas.spec import FieldSpec, FieldType

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "withdrawal": ("withdraw", "withdrawal", "payout", "cash out", "cashout", "take out money"),
    "deposit": ("deposit", "top up", "top-up", "fund my account", "add funds", "transfer in"),
}

# A message with no type keyword is only guessed as "other" when it is at least
# this many words AND contains a concrete problem signal; otherwise it is treated
# as too vague to classify and the engine asks the customer what the issue is about.
_MIN_WORDS_TO_GUESS_OTHER = 6
_PROBLEM_SIGNAL_RE = re.compile(
    r"\b(?:fail(?:ed|ing|s)?|error|not\s+work\w*|doesn'?t\s+work\w*|isn'?t\s+work\w*|"
    r"can'?t|cannot|unable|won'?t|broken|missing|wrong|stuck|declined|charged|"
    r"double[\s-]?charg\w*|logg?ed?\s+out|logging\s+me\s+out|locked\s+out|crash\w*|"
    r"freez\w*|not\s+load\w*|no\s+response|never\s+(?:arrived|received|got|showed)|"
    r"since\s+\w+|days?\s+ago|hours?\s+ago|not\s+showing|disappeared|down\s+for)\b",
    re.IGNORECASE,
)

# Distinctive method aliases (brand / ticker names) that are safe to match on
# their own; generic words like "card" or "bank" require a payment-context cue.
_METHOD_CUE_RE = (
    r"(?:deposit\s+method|payment\s+method|method|paid|pay|sent|deposited|made|"
    r"transferred|via|using|used|through|by|with)"
)

_EMAIL_RE = re.compile(r"[^\s@<>()\[\]]+@[^\s@<>()\[\]]+")
_USER_ID_RE = re.compile(
    r"\b(?:(?:user|customer|account|client)\s*(?:id|number|no\.?|#)|uid|user\s*[:#])"
    r"\s*(?:[:#]\s*|\s+is\s+|\s+)"
    r"([A-Za-z0-9][A-Za-z0-9\-]{1,63})",
    re.IGNORECASE,
)
_TXN_RE = re.compile(
    r"\b(?:transaction|txn|tx|reference|ref|order|hash)\s*"
    r"(?:id|hash|number|no\.?|#)?\s*(?:[:#]\s*|\s+is\s+|\s+)"
    r"([A-Za-z0-9][A-Za-z0-9\-]{3,127})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\b")


class RuleBasedAIProvider(AIProvider):
    name = "rule_based"

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        text = message.lower()
        scores = {opt.type: sum(1 for kw in _KEYWORDS.get(opt.type, ()) if kw in text)
                  for opt in options}
        best_type = max(scores, key=lambda k: scores[k]) if scores else None
        best_score = scores.get(best_type, 0) if best_type else 0

        if best_score > 0:
            return Classification(
                type=best_type,
                confidence=min(0.6 + 0.15 * best_score, 0.95),
                rationale=f"Matched {best_score} keyword(s) for '{best_type}'.",
            )

        # No withdrawal/deposit signal.
        has_other = any(o.type == "other" for o in options)
        substantive = (
            len(message.split()) >= _MIN_WORDS_TO_GUESS_OTHER
            and _PROBLEM_SIGNAL_RE.search(message) is not None
        )
        if has_other and substantive:
            return Classification(
                type="other",
                confidence=0.5,
                rationale="No withdrawal/deposit keywords; concrete problem described -> 'other'.",
            )
        return Classification(
            type=None,
            confidence=0.0,
            rationale="Too vague to classify.",
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
            # ``known`` only ever holds already-validated values. This offline
            # provider does not attempt to re-detect corrections to fields that are
            # already valid (that needs real language understanding - see the
            # Ollama provider); it still re-extracts anything not yet valid, so an
            # invalid value can be corrected.
            if known.get(spec.key):
                continue
            value = self._extract_one(spec, message)
            if value:
                found.append(ExtractedField(key=spec.key, value=value, confidence=0.6))
        return ExtractionResult(fields=found)

    async def summarize(self, transcript: list[str]) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in transcript if line and line.strip()]
        if not lines:
            return ""
        joined = " ".join(lines)
        # Offline fallback: light compression only. A real model produces a proper
        # abstractive summary - see OllamaAIProvider.
        joined = re.sub(r"\s+", " ", joined).strip()
        return (joined[:497] + "...") if len(joined) > 500 else joined

    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        greeting = f"Hi {request.customer_name}," if request.customer_name else "Hello,"
        label = request.complaint_label.lower()

        if request.kind == ReplyKind.ACKNOWLEDGE:
            ref = f" (reference {request.ticket_reference})" if request.ticket_reference else ""
            body = (
                f"{greeting}\n\nThank you - we now have everything we need about your "
                f"{label} and have created a ticket for our team{ref}. "
                "We will be in touch shortly.\n"
            )
            return ReplyDraft(body=body)

        if request.kind == ReplyKind.CLARIFY:
            body = (
                f"{greeting}\n\nThanks for contacting us. We would like to help but need a "
                "little more detail first.\n\n"
                f"{request.guidance or 'Could you describe what happened and what went wrong?'}\n\n"
                "You can reply in your own words - there is no form to fill in.\n"
            )
            return ReplyDraft(body=body)

        # ASK
        parts = [f"{greeting}\n", f"Thanks for getting in touch about your {label}."]
        if request.invalid_fields:
            parts.append("\nSome details we received need correcting:")
            parts.extend(f"  - {f.label}: {f.error}" for f in request.invalid_fields)
        if request.missing_fields:
            parts.append("\nTo move this forward, could you please provide:")
            parts.extend(
                f"  - {s.label}: {s.description}".rstrip() for s in request.missing_fields
            )
        parts.append("\nYou can reply in your own words - no need for a form.\n")
        return ReplyDraft(body="\n".join(parts))

    # ---- helpers ----

    def _extract_one(self, spec: FieldSpec, message: str) -> str | None:
        if spec.type == FieldType.EMAIL:
            m = _EMAIL_RE.search(message)
            return m.group(0).strip(".,;:)") if m else None

        if spec.type == FieldType.DATE:
            m = _DATE_RE.search(message)
            return m.group(1) if m else None

        if spec.type == FieldType.ENUM:
            return self._match_enum(spec, message)

        if spec.key == "user_id":
            m = _USER_ID_RE.search(message)
            if m:
                return m.group(1)

        txn_like = spec.key.endswith("_id") or any(
            tok in spec.key for tok in ("transaction", "txn", "hash")
        )
        if txn_like:
            m = _TXN_RE.search(message)
            if m:
                return m.group(1)

        labelled = self._match_labelled(spec, message)
        if labelled:
            return labelled

        if spec.type == FieldType.TEXT:
            cleaned = re.sub(r"\s+", " ", message).strip()
            return cleaned or None

        return None

    @classmethod
    def _match_enum(cls, spec: FieldSpec, message: str) -> str | None:
        values = spec.validation.enum_values or []
        aliases = spec.validation.enum_aliases or {}

        alias_to_value: dict[str, str] = {}
        for value in values:
            for phrase in [value, value.replace("_", " "), *aliases.get(value, [])]:
                alias_to_value[phrase.lower().strip()] = value
        ordered = sorted(alias_to_value, key=len, reverse=True)
        text = message.lower()

        # 1. Explicit "<label>: <value>" (e.g. "deposit method: card").
        labelled = cls._match_labelled(spec, message)
        if labelled:
            lab = labelled.lower().strip()
            if lab in alias_to_value:
                return alias_to_value[lab]
            for alias in ordered:
                if re.search(rf"\b{re.escape(alias)}\b", lab):
                    return alias_to_value[alias]

        # 2. Multi-word alias, or alias followed by transfer/payment/deposit.
        for alias in ordered:
            if " " in alias and re.search(rf"\b{re.escape(alias)}\b", text):
                return alias_to_value[alias]
            if re.search(rf"\b{re.escape(alias)}\b\s+(?:transfer|payment|deposit)\b", text):
                return alias_to_value[alias]

        # 3. Single-word alias only with a nearby payment-context cue.
        for alias in ordered:
            if " " in alias:
                continue
            if re.search(
                rf"\b{_METHOD_CUE_RE}\b[\w\s]{{0,20}}?\b{re.escape(alias)}\b", text
            ):
                return alias_to_value[alias]
        return None

    @staticmethod
    def _match_labelled(spec: FieldSpec, message: str) -> str | None:
        names = {spec.label.lower(), spec.key.lower(), spec.key.replace("_", " ").lower()}
        alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        pattern = (
            rf"(?:{alt})\s*(?:is|are|:|=|#|-)\s*"
            r"['\"]?([^\n,;]+?)['\"]?\s*(?=[,;\n]|\.(?:\s|$)|$)"
        )
        m = re.search(pattern, message, re.IGNORECASE)
        if not m:
            return None
        return m.group(1).strip(" '\".")
