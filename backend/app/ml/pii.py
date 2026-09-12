"""Masking identifiers before text is handed to a model or exported.

Embeddings, similarity and incident clustering should be driven by what a
complaint is *about*, not by the account number or address in it - an ID in
the text would make two unrelated tickets from the same customer look alike,
and put personal data into every derived artifact. Masking also means an
exported training example carries no direct identifier.

Conservative on purpose: it replaces things that are structurally
identifiers (addresses, URLs, digit-bearing codes, long numbers, phone-like
digit runs) and leaves words alone. It is not a named-entity recogniser and
does not claim to remove names.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[^\s@<>()\[\]]+@[^\s@<>()\[\]]+\.[^\s@<>()\[\]]+")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# A run of digits with optional spaces/dashes, long enough to be a card,
# account or phone number rather than an amount or a day.
_LONG_NUMBER_RE = re.compile(r"(?<!\w)\+?\d(?:[\d\s-]{6,}\d)(?!\w)")
# A code mixing letters and digits (U-482913, TXN-9f3a12bc, 0x3fA9...).
_CODE_RE = re.compile(r"(?<!\w)(?=[\w-]*\d)(?=[\w-]*[^\W\d_])[\w-]{4,}(?!\w)")
# Pure digit tokens of 5+ (user ids like 583921) that the above missed.
_DIGITS_RE = re.compile(r"(?<!\w)\d{5,}(?!\w)")


def mask_identifiers(text: str) -> str:
    text = _EMAIL_RE.sub("[email]", text or "")
    text = _URL_RE.sub("[url]", text)
    text = _LONG_NUMBER_RE.sub("[number]", text)
    text = _CODE_RE.sub("[id]", text)
    return _DIGITS_RE.sub("[number]", text)
