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
    LanguageDetection,
    ReplyDraft,
    ReplyKind,
    ReplyRequest,
    TypeOption,
)
from app.domain.complaint_schemas.spec import FieldSpec, FieldType

# Keyword lists are intentionally multilingual (English / Russian / Arabic) so
# this offline provider can classify complaints regardless of the customer's
# language - see app/ai/providers/rule_based.py::detect_language below for the
# language side of the same requirement. This is classification vocabulary,
# not a canned response: the reply text is still built from the engine's
# dynamic ReplyRequest (kind, fields, ticket reference, ...), never a fixed
# string keyed on language.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "withdrawal": (
        "withdraw", "withdrawal", "payout", "cash out", "cashout", "take out money",
        "вывод", "вывести", "снятие", "снять",  # Russian
        "سحب",  # Arabic
    ),
    "deposit": (
        "deposit", "top up", "top-up", "fund my account", "add funds", "transfer in",
        "депозит", "пополнение", "пополнить", "внести",  # Russian
        "إيداع", "ايداع",  # Arabic
    ),
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

# Free-text deposit method ("bank transfer", "PayPal", "my local payment wallet").
# We capture verbatim what the customer said - no mapping to any category.
_DEPOSIT_METHOD_RES = (
    re.compile(r"(?:deposit|payment)\s+method\s*(?:was|is|:|-)?\s*([^\n,.;]{2,60})", re.IGNORECASE),
    re.compile(r"\bmethod\s*(?:was|is|:|-)\s*([^\n,.;]{2,60})", re.IGNORECASE),
    re.compile(
        r"\b(?:deposited|paid|sent|transferred|"
        r"made\s+(?:the|my|a)\s+(?:deposit|payment|transfer))\b[^\n.;,]*?"
        r"\b(?:using|via|through|with|by)\s+(?:my\s+|a\s+|an\s+|the\s+)?([^\n,.;]{2,60})",
        re.IGNORECASE,
    ),
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

# ---- language detection --------------------------------------------------
# Script-based heuristic: no model, no network - just Unicode block counting.
# It reliably tells apart the three languages this offline provider supports
# (English, Russian, Arabic) for the prototype. It is NOT a general-purpose
# language identifier: any other Latin-script language (French, Spanish, ...)
# will be classified as "en". Real multilingual detection is the Ollama
# provider's job (see OllamaAIProvider.detect_language).
_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿ]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# ---- localized reply scaffolding ------------------------------------------
# Only the surrounding natural-language scaffold is localized here - the
# dynamic content (complaint label, field labels/descriptions from the YAML
# schema, ticket reference, customer name) is never translated. This keeps
# the provider free of any fixed "language -> full response" template while
# still letting it produce a natural reply in the customer's language.
_DEFAULT_LANGUAGE = "en"
_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "greeting_named": "Hi {name},",
        "greeting": "Hello,",
        "ack": (
            "Thank you - we now have everything we need about your {label} and have "
            "created a ticket for our team{ref}. We will be in touch shortly."
        ),
        "ref_suffix": " (reference {ref})",
        "ask_intro": "Thanks for getting in touch about your {label}.",
        "invalid_intro": "Some details we received need correcting:",
        "missing_intro": "To move this forward, could you please provide:",
        "footer": "You can reply in your own words - no need for a form.",
        "clarify_intro": (
            "Thanks for contacting us. We would like to help but need a little more "
            "detail first."
        ),
        "clarify_question": "Could you describe what happened and what went wrong?",
    },
    "ru": {
        "greeting_named": "Здравствуйте, {name},",
        "greeting": "Здравствуйте,",
        "ack": (
            "Спасибо - теперь у нас есть всё необходимое по вашему обращению "
            "«{label}», и мы создали заявку для нашей команды{ref}. Мы свяжемся с "
            "вами в ближайшее время."
        ),
        "ref_suffix": " (номер {ref})",
        "ask_intro": "Спасибо, что обратились к нам по поводу «{label}».",
        "invalid_intro": "Некоторые из полученных данных нужно исправить:",
        "missing_intro": "Чтобы продолжить, пожалуйста, предоставьте:",
        "footer": "Вы можете ответить своими словами - заполнять форму не нужно.",
        "clarify_intro": (
            "Спасибо, что написали нам. Мы хотим помочь, но сначала нужно немного "
            "больше деталей."
        ),
        "clarify_question": "Не могли бы вы описать, что произошло и что пошло не так?",
    },
    "ar": {
        "greeting_named": "مرحبًا {name}،",
        "greeting": "مرحبًا،",
        "ack": (
            "شكرًا لك - أصبح لدينا الآن كل ما نحتاجه بخصوص {label}، وقد أنشأنا "
            "تذكرة لفريقنا{ref}. سنتواصل معك قريبًا."
        ),
        "ref_suffix": " (المرجع {ref})",
        "ask_intro": "شكرًا لتواصلك معنا بخصوص {label}.",
        "invalid_intro": "بعض التفاصيل التي استلمناها تحتاج إلى تصحيح:",
        "missing_intro": "لإتمام الأمر، يرجى تزويدنا بما يلي:",
        "footer": "يمكنك الرد بأسلوبك الخاص - لا حاجة لتعبئة نموذج.",
        "clarify_intro": (
            "شكرًا لتواصلك معنا. نود مساعدتك، لكننا بحاجة إلى مزيد من التفاصيل أولاً."
        ),
        "clarify_question": "هل يمكنك وصف ما حدث وما الذي حدث بشكل خاطئ؟",
    },
}


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

    async def detect_language(self, message: str) -> LanguageDetection:
        arabic = len(_ARABIC_RE.findall(message))
        cyrillic = len(_CYRILLIC_RE.findall(message))
        latin = len(_LATIN_RE.findall(message))

        # Arabic/Cyrillic script is a strong, hard-to-fake signal that the
        # customer is writing in that language - a message essentially never
        # contains those characters otherwise. Plain Latin/ASCII text (email
        # addresses, IDs, common support words like "user id") is
        # comparatively weak evidence: it shows up in almost every message
        # regardless of the customer's language (universal technical
        # tokens), so it never outweighs an actual non-Latin script
        # presence, however much of the message it makes up - a bilingual
        # customer who writes mostly Arabic but pastes an English email
        # address is still writing Arabic.
        if arabic > 0 and arabic >= cyrillic:
            return LanguageDetection(code="ar", confidence=min(0.75 + 0.03 * arabic, 0.99))
        if cyrillic > 0:
            return LanguageDetection(code="ru", confidence=min(0.75 + 0.03 * cyrillic, 0.99))
        if latin > 0:
            return LanguageDetection(
                code=_DEFAULT_LANGUAGE, confidence=min(0.6 + 0.02 * latin, 0.99)
            )
        return LanguageDetection(code=None, confidence=0.0)

    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        p = _PHRASES.get(request.language_code, _PHRASES[_DEFAULT_LANGUAGE])
        greeting = (
            p["greeting_named"].format(name=request.customer_name)
            if request.customer_name
            else p["greeting"]
        )
        label = request.complaint_label.lower()

        if request.kind == ReplyKind.ACKNOWLEDGE:
            ref = (
                p["ref_suffix"].format(ref=request.ticket_reference)
                if request.ticket_reference
                else ""
            )
            body = f"{greeting}\n\n{p['ack'].format(label=label, ref=ref)}\n"
            return ReplyDraft(body=body)

        if request.kind == ReplyKind.CLARIFY:
            # `request.guidance` is a free-text instruction meant for a real
            # language model (see OllamaAIProvider) - this offline provider
            # cannot translate arbitrary guidance text, so it always asks its
            # own localized generic clarifying question instead.
            body = (
                f"{greeting}\n\n{p['clarify_intro']}\n\n"
                f"{p['clarify_question']}\n\n{p['footer']}\n"
            )
            return ReplyDraft(body=body)

        # ASK
        parts = [f"{greeting}\n", p["ask_intro"].format(label=label)]
        if request.invalid_fields:
            parts.append(f"\n{p['invalid_intro']}")
            parts.extend(f"  - {f.label}: {f.error}" for f in request.invalid_fields)
        if request.missing_fields:
            parts.append(f"\n{p['missing_intro']}")
            parts.extend(
                f"  - {s.label}: {s.description}".rstrip() for s in request.missing_fields
            )
        parts.append(f"\n{p['footer']}\n")
        return ReplyDraft(body="\n".join(parts))

    # ---- helpers ----

    def _extract_one(self, spec: FieldSpec, message: str) -> str | None:
        if spec.type == FieldType.EMAIL:
            m = _EMAIL_RE.search(message)
            return m.group(0).strip(".,;:)") if m else None

        if spec.type == FieldType.DATE:
            m = _DATE_RE.search(message)
            return m.group(1) if m else None

        if spec.key == "deposit_method":
            return self._match_free_text_method(message) or self._match_labelled(spec, message)

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

    @staticmethod
    def _match_free_text_method(message: str) -> str | None:
        for pattern in _DEPOSIT_METHOD_RES:
            m = pattern.search(message)
            if m:
                return m.group(1).strip(" '\".")
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
