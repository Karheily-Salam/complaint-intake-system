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
from datetime import date

from app.ai.base import (
    AIProvider,
    Classification,
    CollectedFieldView,
    ExtractedField,
    ExtractionResult,
    InvalidField,
    LanguageDetection,
    ReplyDraft,
    ReplyKind,
    ReplyRequest,
    TypeOption,
)
from app.domain.complaint_schemas.spec import FieldSpec, FieldType
from app.domain.dates import MONTH_NAMES, YEAR_TOKEN_RE

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

# ---- pending-field contextual fallback -------------------------------------
# This is an email conversation, not a sequence of independent messages: when
# the previous outbound message asked for one specific field (see
# ConversationEngine.advance's `pending_field`), the customer's reply is
# interpreted primarily as an answer to THAT field if nothing more specific
# claimed it above. This is type/shape-driven (see _pending_field_fallback),
# never a per-field special case.


def _looks_transaction_related(key: str) -> bool:
    """True only for fields that are actually about a transaction/reference
    (e.g. withdrawal_transaction_id) - used to decide whether `_TXN_RE`'s
    transaction-specific trigger words are relevant to this field at all.
    Deliberately narrower than :func:`_is_identifier_like_key` below: a field
    merely ending in "_id" (like ``user_id``) is not transaction-related, and
    must never have a transaction mention elsewhere in the message stolen
    for it.
    """
    return any(tok in key for tok in ("transaction", "txn", "hash", "reference", "ref"))


def _is_identifier_like_key(key: str) -> bool:
    """True for short-code fields (user_id, withdrawal_transaction_id, ...) as
    opposed to free-text fields (deposit_method, source_wallet_or_account).

    Broader than :func:`_looks_transaction_related` on purpose: this only
    decides whether the *pending-field* fallback should look for a generic
    digit-bearing token (never which other field's regex to try), so there is
    no cross-field contamination risk in being inclusive here.
    """
    return key.endswith("_id") or _looks_transaction_related(key)


# A bare value with no label at all - e.g. just "583921" - could be a user id,
# a transaction id, an amount, anything. Only the pending field tells us
# which. Requires at least one digit so plain words ("hello", "yes", "ID")
# are never mistaken for a code; the FIRST such token is used (the customer's
# earliest, most direct answer to what was just asked, matching the "primary
# response to the most recent request" principle even when a later part of
# the same message goes on to mention something else numeric).
_VALUE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-]{1,63}\b")


def _first_value_token(message: str) -> str | None:
    for m in _VALUE_TOKEN_RE.finditer(message):
        token = m.group(0)
        if any(ch.isdigit() for ch in token):
            return token
    return None


# Common short greetings/acknowledgements in the three supported languages -
# generic conversational filler, not a specific field's business vocabulary.
# Guards the free-text fallback (deposit_method, source_wallet_or_account)
# from mistaking a non-answer for an answer, the same way `_first_value_token`
# requiring a digit guards the identifier fallback.
_FILLER_REPLIES = {
    "hello", "hi", "hey", "hiya", "yes", "no", "ok", "okay", "thanks", "thank you",
    "مرحبا", "مرحباً", "اهلا", "أهلا", "نعم", "لا", "شكرا", "شكراً",
    "привет", "здравствуйте", "да", "нет", "спасибо", "ок",
}


def _is_pure_filler(cleaned_message: str) -> bool:
    normalized = cleaned_message.strip(" .!؟?,،-").lower()
    return not normalized or normalized in _FILLER_REPLIES or not any(
        ch.isalnum() for ch in normalized
    )


# Minimal multilingual month-name table so a natural date like "8 September" /
# "8 сентября" / "8 سبتمبر" resolves even without a numeric date format -
# the schema's own extraction_hint already asks for this ("accept relative
# references like 'yesterday' if resolvable"). No year is ever stated in
# these phrasings, so the current year is assumed. The table lives in
# app.domain.dates so the evidence locator reads dates exactly as this does.
_MONTH_NAMES = MONTH_NAMES
_DATE_WORD_RE = re.compile(r"[^\s,،.]+")


_YEAR_TOKEN_RE = YEAR_TOKEN_RE


def _parse_natural_date(message: str) -> str | None:
    """Resolve a written date like "8 September" / "8 сентября" / "8 سبتمبر".

    Three rules keep this from inventing data, each one a production defect
    that actually occurred or was one step away:

    * a day must be a one- or two-digit token, so a long number that happens
      to sit beside a month name (a phone number, a transaction id) can never
      be read as a day;
    * an explicitly written four-digit year is used. Assuming the current year
      when the customer wrote "Sep 11, 2024" silently stored the wrong date;
    * the result must be a real calendar date, so "31 February" is rejected
      here rather than becoming a valid-looking value downstream.

    The current year is still assumed for the bare "8 September" phrasing the
    schema's extraction_hint invites - but only when no year is stated.
    """
    words = _DATE_WORD_RE.findall(message)
    for i, word in enumerate(words):
        month = _MONTH_NAMES.get(word.strip(".,،").lower())
        if month is None:
            continue

        day: int | None = None
        year: int | None = None
        # Only the immediate neighbourhood of the month name: "11 Sep 2026"
        # and "Sep 11, 2026" both fit, while a number further down the message
        # is not silently adopted.
        for j in (i - 2, i - 1, i + 1, i + 2):
            if not (0 <= j < len(words)):
                continue
            digits = re.sub(r"\D", "", words[j])
            if not digits:
                continue
            if _YEAR_TOKEN_RE.match(digits):
                if year is None:
                    year = int(digits)
            elif day is None and len(digits) <= 2 and 1 <= int(digits) <= 31:
                day = int(digits)

        if day is None:
            continue
        try:
            resolved = date(year if year is not None else date.today().year, month, day)
        except ValueError:
            continue  # e.g. 31 February - not a date, so not a value
        return resolved.strftime("%Y-%m-%d")
    return None

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
        "ack_intro": "Thank you - we now have all the information we need about your issue:",
        "ack_sent_to_team": (
            "This has been sent to our specialist team to review and resolve the "
            "issue, and we will be in touch with you shortly."
        ),
        "ack_reference_label": "Issue reference number",
        "footer": "You can reply in your own words - no need for a form.",
        "clarify_intro": (
            "Thanks for contacting us. We would like to help but need a little more "
            "detail first."
        ),
        "clarify_question": "Could you describe what happened and what went wrong?",
        "ask_field_generic": "Could you please provide the following: {item}?",
        "invalid_field_generic": (
            'The information you provided for "{item}" doesn\'t look right. '
            "Could you please provide it again?"
        ),
    },
    "ru": {
        "greeting_named": "Здравствуйте, {name},",
        "greeting": "Здравствуйте,",
        "ack_intro": (
            "Спасибо - теперь у нас есть вся необходимая информация по вашему обращению:"
        ),
        "ack_sent_to_team": (
            "Эта информация передана специализированной команде для рассмотрения и "
            "решения проблемы, и мы свяжемся с вами в ближайшее время."
        ),
        "ack_reference_label": "Номер обращения",
        "footer": "Вы можете ответить своими словами - заполнять форму не нужно.",
        "clarify_intro": (
            "Спасибо, что написали нам. Мы хотим помочь, но сначала нужно немного "
            "больше деталей."
        ),
        "clarify_question": "Не могли бы вы описать, что произошло и что пошло не так?",
        "ask_field_generic": "Пожалуйста, укажите следующее: {item}.",
        "invalid_field_generic": (
            'Указанные данные ("{item}") выглядят некорректно. '
            "Пожалуйста, укажите их ещё раз."
        ),
    },
    "ar": {
        "greeting_named": "مرحبًا {name}،",
        "greeting": "مرحبًا،",
        "ack_intro": "شكرًا لك، أصبحت لدينا جميع المعلومات المطلوبة بخصوص مشكلتك:",
        "ack_sent_to_team": (
            "تم إرسال هذه المعلومات إلى الفريق المختص لمراجعة المشكلة وحلها، "
            "وسيتم التواصل معك قريبًا."
        ),
        "ack_reference_label": "الرقم المرجعي للمشكلة",
        "footer": "يمكنك الرد بأسلوبك الخاص - لا حاجة لتعبئة نموذج.",
        "clarify_intro": (
            "شكرًا لتواصلك معنا. نود مساعدتك، لكننا بحاجة إلى مزيد من التفاصيل أولاً."
        ),
        "clarify_question": "هل يمكنك وصف ما حدث وما الذي حدث بشكل خاطئ؟",
        "ask_field_generic": "يرجى تزويدنا بما يلي: {item}.",
        "invalid_field_generic": (
            "المعلومات التي أدخلتها لـ«{item}» غير صحيحة. يرجى إدخالها مرة أخرى."
        ),
    },
}

# ---- one-field-at-a-time question phrasing --------------------------------
# Keyed by internal field `key` (unchanged, never exposed to the customer) ->
# a natural, standalone, single-sentence question per supported language.
# This is presentation vocabulary the AI layer owns, not business logic: the
# engine (see ConversationEngine.advance / _classify_fields) is what decides
# WHICH single field is still missing and asks for it, in schema order, one
# per turn - this table only decides HOW to phrase that one field's question.
# A key not listed here (e.g. a future schema field) falls back to
# `_PHRASES[lang]["ask_field_generic"]` built from the field's own
# label/description, so this never raises for an unknown key.
_FIELD_QUESTION_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "user_id": "Please provide your user ID.",
        "account_email": "Please provide the email address registered on your account.",
        "withdrawal_transaction_id": "Please provide the withdrawal transaction ID.",
        "source_wallet_or_account": "Please let us know which wallet or account you used.",
        "transaction_date": "Please let us know the date of the transaction.",
        "deposit_method": (
            "Please let us know how you made the deposit (e.g. bank transfer, card, "
            "or e-wallet)."
        ),
        "problem_description": "Please describe what happened in a bit more detail.",
    },
    "ru": {
        "user_id": "Пожалуйста, укажите ваш ID пользователя.",
        "account_email": "Пожалуйста, укажите email, привязанный к вашему аккаунту.",
        "withdrawal_transaction_id": "Пожалуйста, укажите номер транзакции вывода средств.",
        "source_wallet_or_account": (
            "Пожалуйста, укажите, с какого кошелька или счёта вы производили операцию."
        ),
        "transaction_date": "Пожалуйста, укажите дату операции.",
        "deposit_method": (
            "Пожалуйста, укажите способ внесения депозита (например, банковский "
            "перевод, карта или электронный кошелёк)."
        ),
        "problem_description": "Пожалуйста, опишите подробнее, что произошло.",
    },
    "ar": {
        "user_id": "عزيزي المستخدم، يرجى تزويدنا برقم المستخدم الخاص بحسابك.",
        "account_email": "يرجى تزويدنا بالبريد الإلكتروني المرتبط بحسابك.",
        "withdrawal_transaction_id": "يرجى تزويدنا برقم عملية السحب.",
        "source_wallet_or_account": "يرجى إخبارنا بالمحفظة أو الحساب الذي استخدمته.",
        "transaction_date": "يرجى تزويدنا بتاريخ العملية.",
        "deposit_method": (
            "يرجى إخبارنا بطريقة الإيداع التي استخدمتها (مثل التحويل البنكي أو "
            "البطاقة أو المحفظة الإلكترونية)."
        ),
        "problem_description": "يرجى وصف ما حدث بمزيد من التفاصيل.",
    },
}


def _field_question(spec: FieldSpec, language_code: str) -> str:
    table = _FIELD_QUESTION_PHRASES.get(language_code, _FIELD_QUESTION_PHRASES[_DEFAULT_LANGUAGE])
    phrase = table.get(spec.key)
    if phrase:
        return phrase
    generic = _PHRASES.get(language_code, _PHRASES[_DEFAULT_LANGUAGE])["ask_field_generic"]
    return generic.format(item=spec.description or spec.label)


# ---- natural-language display labels (final confirmation) -----------------
# Customer-facing NOUN labels for the ticket confirmation's collected-info
# list - deliberately not a literal translation of the schema's English
# `label`/internal `key` (e.g. "withdrawal_transaction_id" never appears).
# Keyed by field key, same fallback pattern as _FIELD_QUESTION_PHRASES: a key
# not listed here falls back to the schema's own (English) label rather than
# the raw key, so an unknown future field still never leaks its key.
_FIELD_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "user_id": "User ID",
        "account_email": "Account email",
        "withdrawal_transaction_id": "Withdrawal transaction number",
        "source_wallet_or_account": "Source wallet or account",
        "transaction_date": "Transaction date",
        "deposit_method": "Deposit method",
    },
    "ru": {
        "user_id": "ID пользователя",
        "account_email": "Электронная почта",
        "withdrawal_transaction_id": "Номер транзакции вывода",
        "source_wallet_or_account": "Кошелёк или счёт списания",
        "transaction_date": "Дата операции",
        "deposit_method": "Способ внесения депозита",
    },
    "ar": {
        "user_id": "رقم المستخدم",
        "account_email": "البريد الإلكتروني",
        "withdrawal_transaction_id": "رقم عملية السحب",
        "source_wallet_or_account": "المحفظة أو الحساب المستخدم",
        "transaction_date": "تاريخ العملية",
        "deposit_method": "طريقة الإيداع",
    },
}


def _field_display_label(field: CollectedFieldView, language_code: str) -> str:
    table = _FIELD_DISPLAY_LABELS.get(language_code, _FIELD_DISPLAY_LABELS[_DEFAULT_LANGUAGE])
    return table.get(field.key, field.label)


# ---- invalid-answer correction phrasing ------------------------------------
# Natural, customer-friendly explanations of what was wrong and what to send
# instead - never the raw validation error text (e.g. "Not a valid email
# address.") or the internal field key. Keyed by field key, same fallback
# pattern as _FIELD_QUESTION_PHRASES.
_FIELD_INVALID_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "user_id": (
            "The user ID you provided doesn't look valid. Please provide the "
            "correct user ID for your account."
        ),
        "account_email": (
            "The email address you provided isn't in a valid format. Please provide "
            "the email address registered on your account, for example: "
            "example@example.com"
        ),
        "withdrawal_transaction_id": (
            "The withdrawal transaction number you provided doesn't look valid. "
            "Please provide the correct withdrawal transaction number."
        ),
        "source_wallet_or_account": (
            "We couldn't quite make out the wallet or account you used. Could you "
            "tell us again which one it was?"
        ),
        "transaction_date": (
            "The date you provided doesn't look valid. Please provide the date in "
            "the format YYYY-MM-DD, for example: 2026-09-08."
        ),
        "deposit_method": (
            "We couldn't quite make out how you made the deposit. Could you tell us "
            "again (e.g. bank transfer, card, or e-wallet)?"
        ),
    },
    "ru": {
        "user_id": (
            "Указанный ID пользователя недействителен. Пожалуйста, укажите "
            "правильный ID пользователя для вашего аккаунта."
        ),
        "account_email": (
            "Указанный адрес электронной почты имеет неверный формат. Пожалуйста, "
            "укажите email, привязанный к вашему аккаунту, например: "
            "example@example.com"
        ),
        "withdrawal_transaction_id": (
            "Указанный номер транзакции вывода недействителен. Пожалуйста, укажите "
            "правильный номер транзакции вывода средств."
        ),
        "source_wallet_or_account": (
            "Не удалось разобрать, с какого кошелька или счёта была проведена "
            "операция. Пожалуйста, укажите ещё раз."
        ),
        "transaction_date": (
            "Указанная дата недействительна. Пожалуйста, укажите дату в формате "
            "ГГГГ-ММ-ДД, например: 2026-09-08."
        ),
        "deposit_method": (
            "Не удалось разобрать способ внесения депозита. Пожалуйста, укажите "
            "ещё раз (например, банковский перевод, карта или электронный кошелёк)."
        ),
    },
    "ar": {
        "user_id": (
            "عزيزي المستخدم، رقم المستخدم الذي أدخلته غير صحيح. يرجى إدخال رقم "
            "المستخدم الصحيح الخاص بحسابك."
        ),
        "account_email": (
            "عزيزي المستخدم، صيغة البريد الإلكتروني التي أدخلتها غير صحيحة.\n"
            "يرجى إدخال البريد الإلكتروني المرتبط بحسابك بالشكل التالي:\n"
            "example@example.com"
        ),
        "withdrawal_transaction_id": (
            "رقم عملية السحب الذي أدخلته غير صحيح. يرجى إدخال رقم عملية السحب الصحيح."
        ),
        "source_wallet_or_account": (
            "لم نتمكن من التعرف على المحفظة أو الحساب الذي استخدمته. يرجى إخبارنا "
            "مرة أخرى."
        ),
        "transaction_date": (
            "التاريخ الذي أدخلته غير صحيح. يرجى إدخال التاريخ بالصيغة التالية: "
            "2026-09-08."
        ),
        "deposit_method": (
            "لم نتمكن من التعرف على طريقة الإيداع التي استخدمتها. يرجى إخبارنا مرة "
            "أخرى (مثل التحويل البنكي أو البطاقة أو المحفظة الإلكترونية)."
        ),
    },
}


def _field_invalid_message(invalid: InvalidField, language_code: str) -> str:
    table = _FIELD_INVALID_PHRASES.get(language_code, _FIELD_INVALID_PHRASES[_DEFAULT_LANGUAGE])
    phrase = table.get(invalid.key)
    if phrase:
        return phrase
    generic = _PHRASES.get(language_code, _PHRASES[_DEFAULT_LANGUAGE])["invalid_field_generic"]
    return generic.format(item=invalid.label)


def keyword_classification(message: str, options: list[TypeOption]) -> Classification | None:
    """The deterministic part of classification: an explicit type keyword.

    Returns None when no withdrawal/deposit keyword is present. Exposed on its
    own so the hybrid provider can treat a keyword match as authoritative
    while letting the statistical classifier handle only what this misses.
    """
    text = message.lower()
    scores = {opt.type: sum(1 for kw in _KEYWORDS.get(opt.type, ()) if kw in text)
              for opt in options}
    best_type = max(scores, key=lambda k: scores[k]) if scores else None
    best_score = scores.get(best_type, 0) if best_type else 0
    if best_score <= 0:
        return None
    return Classification(
        type=best_type,
        confidence=min(0.6 + 0.15 * best_score, 0.95),
        rationale=f"Matched {best_score} keyword(s) for '{best_type}'.",
        decided_by="rules",
    )


class RuleBasedAIProvider(AIProvider):
    name = "rule_based"

    async def classify(self, message: str, options: list[TypeOption]) -> Classification:
        matched = keyword_classification(message, options)
        if matched is not None:
            return matched

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
                decided_by="rules",
            )
        return Classification(
            type=None,
            confidence=0.0,
            rationale="Too vague to classify.",
            decided_by="rules",
        )

    async def extract(
        self,
        message: str,
        specs: list[FieldSpec],
        known: dict[str, str] | None = None,
        pending_field: str | None = None,
    ) -> ExtractionResult:
        known = known or {}
        found: list[ExtractedField] = []
        resolved_keys: set[str] = set()
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
                resolved_keys.add(spec.key)

        # Contextual fallback: the previous outbound message asked
        # specifically for `pending_field`. If nothing above already
        # resolved it (no label, no type-specific pattern - e.g. a bare
        # "583921" or a natural sentence with no recognizable keyword),
        # interpret the customer's reply primarily as the answer to that one
        # field. This never overrides a value found above, and never blocks
        # any other field also found above - it only fills in the one field
        # nothing else claimed.
        if pending_field and pending_field not in resolved_keys and not known.get(pending_field):
            pending_spec = next((s for s in specs if s.key == pending_field), None)
            if pending_spec is not None:
                value = self._pending_field_fallback(pending_spec, message)
                if value:
                    found.append(
                        ExtractedField(key=pending_spec.key, value=value, confidence=0.55)
                    )

        return ExtractionResult(fields=found)

    def _pending_field_fallback(self, spec: FieldSpec, message: str) -> str | None:
        """Best-effort value for `spec` when it is the pending field and no
        more specific extractor (label, email, numeric date, ...) matched.

        Dispatches purely on the field's declared `type`/key shape - never on
        which specific field this is - so the same logic applies to every
        required field, present or future.
        """
        if spec.type == FieldType.EMAIL:
            return None  # the unconditional email pass above already covers this
        if spec.type == FieldType.DATE:
            return _parse_natural_date(message)
        if spec.type == FieldType.TEXT:
            return None  # already has its own unconditional whole-message fallback
        if spec.type in (FieldType.STRING, FieldType.NUMBER):
            if _is_identifier_like_key(spec.key):
                return _first_value_token(message)
            # Free-text field (e.g. deposit_method, source_wallet_or_account):
            # the whole message is itself the answer, same idea as the
            # existing TEXT-type fallback - unless it's just a greeting/ack.
            cleaned = re.sub(r"\s+", " ", message).strip()
            return None if _is_pure_filler(cleaned) else (cleaned or None)
        return None

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
            # Deliberately slow-growing and low-baseline compared to
            # Arabic/Cyrillic above: a short reply that is little more than
            # a copied field label and a code (e.g. "transaction id:
            # TXN-9f3a12bc") is exactly the kind of message our own
            # regex-based field extraction requires the customer to send
            # (see _USER_ID_RE / _TXN_RE), so it must NOT count as confident
            # evidence that an otherwise Arabic/Russian conversation just
            # switched to English - it should fall below
            # `settings.min_language_confidence` and let the engine keep the
            # conversation's established language. Only a message with
            # substantially more Latin text than that (genuine English
            # prose) clears the bar. Because English is also the engine's
            # ultimate default when nothing is confident, an English message
            # that fails to clear this bar still resolves correctly via that
            # default/previous-language fallback - so this asymmetry has no
            # downside for real English conversations.
            return LanguageDetection(
                code=_DEFAULT_LANGUAGE, confidence=min(0.25 + 0.01 * latin, 0.95)
            )
        return LanguageDetection(code=None, confidence=0.0)

    async def compose_reply(self, request: ReplyRequest) -> ReplyDraft:
        p = _PHRASES.get(request.language_code, _PHRASES[_DEFAULT_LANGUAGE])
        greeting = (
            p["greeting_named"].format(name=request.customer_name)
            if request.customer_name
            else p["greeting"]
        )

        if request.kind == ReplyKind.ACKNOWLEDGE:
            lines = [greeting, "", p["ack_intro"], ""]
            lines.extend(
                f"{_field_display_label(f, request.language_code)}: {f.value}"
                for f in request.collected_fields
            )
            lines.append("")
            lines.append(p["ack_sent_to_team"])
            if request.ticket_reference:
                lines.append("")
                lines.append(f"{p['ack_reference_label']}: {request.ticket_reference}")
            return ReplyDraft(body="\n".join(lines) + "\n")

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

        # ASK - the engine guarantees at most one entry total across
        # missing_fields/invalid_fields (see ConversationEngine.advance,
        # which resolves exactly one next-unresolved field per turn). A
        # short, standalone, natural message: no greeting scaffold, no
        # bullet list, no internal field name or raw validation error text -
        # this should read like a reply in an email thread, not a form.
        if request.invalid_fields:
            correction = _field_invalid_message(request.invalid_fields[0], request.language_code)
            return ReplyDraft(body=f"{correction}\n")

        question = _field_question(request.missing_fields[0], request.language_code)
        return ReplyDraft(body=f"{question}\n")

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

        if _looks_transaction_related(spec.key):
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
