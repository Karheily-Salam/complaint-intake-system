"""Finding dates in customer text, with the exact span each came from.

Shared by the rule-based extractor (which only needs *a* date) and the
evidence locator (which needs to prove *where* a date came from). Keeping the
month vocabulary in one place means the two can never disagree about what
counts as a written date.

Every function returns ISO ``YYYY-MM-DD`` strings and only real calendar
dates - "31 February" is not a date, so it is never returned.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# Minimal multilingual month-name table so a natural date like "8 September" /
# "8 сентября" / "8 سبتمبر" resolves even without a numeric date format.
MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "января": 1, "январь": 1, "февраля": 2, "февраль": 2, "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4, "мая": 5, "май": 5, "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7, "августа": 8, "август": 8, "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10, "ноября": 11, "ноябрь": 11, "декабря": 12, "декабрь": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5,
    "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}

YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")
_WORD_RE = re.compile(r"[^\s,،.]+")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b")

# Relative day words -> offset from the reference date. Longest phrases are
# matched first so "day before yesterday" never also counts as "yesterday".
_RELATIVE: tuple[tuple[str, int], ...] = (
    ("the day before yesterday", -2),
    ("day before yesterday", -2),
    ("позавчера", -2),
    ("أول أمس", -2),
    ("اول امس", -2),
    ("yesterday", -1),
    ("вчера", -1),
    ("البارحة", -1),
    ("أمس", -1),
    ("امس", -1),
    ("today", 0),
    ("сегодня", 0),
    ("اليوم", 0),
)


def natural_date_spans(message: str, today: date | None = None) -> list[tuple[int, int, str]]:
    """Written dates ("8 September", "Sep 11, 2024", "8 сентября") with spans.

    Same rules as the rule-based extractor: the day is a one- or two-digit
    token right next to the month name, an explicit four-digit year is
    honoured, and the current year is assumed only when none is written.
    """
    today = today or date.today()
    words = list(_WORD_RE.finditer(message))
    found: list[tuple[int, int, str]] = []
    for i, word in enumerate(words):
        month = MONTH_NAMES.get(word.group().strip(".,،").lower())
        if month is None:
            continue
        day: int | None = None
        year: int | None = None
        used = [i]
        for j in (i - 2, i - 1, i + 1, i + 2):
            if not 0 <= j < len(words):
                continue
            digits = re.sub(r"\D", "", words[j].group())
            if not digits:
                continue
            if YEAR_TOKEN_RE.match(digits):
                if year is None:
                    year = int(digits)
                    used.append(j)
            elif day is None and len(digits) <= 2 and 1 <= int(digits) <= 31:
                day = int(digits)
                used.append(j)
        if day is None:
            continue
        try:
            resolved = date(year if year is not None else today.year, month, day)
        except ValueError:
            continue
        start = min(words[k].start() for k in used)
        end = max(words[k].end() for k in used)
        found.append((start, end, resolved.isoformat()))
    return found


def numeric_date_spans(message: str, formats: list[str]) -> list[tuple[int, int, str]]:
    """Numeric dates in any of ``formats`` (plus ISO), with spans."""
    accepted = list(dict.fromkeys(["%Y-%m-%d", *formats]))
    found = []
    for match in _NUMERIC_DATE_RE.finditer(message):
        for fmt in accepted:
            try:
                parsed = datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                continue
            found.append((match.start(1), match.end(1), parsed.isoformat()))
            break
    return found


def relative_date_spans(message: str, today: date | None = None) -> list[tuple[int, int, str]]:
    """"yesterday" / "вчера" / "أمس" and friends, resolved against ``today``."""
    today = today or date.today()
    lowered = message.lower()
    taken: list[tuple[int, int]] = []
    found = []
    for phrase, offset in _RELATIVE:
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered):
            span = (match.start(), match.end())
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            found.append((*span, (today + timedelta(days=offset)).isoformat()))
    return found


def date_spans(
    message: str, formats: list[str], today: date | None = None
) -> list[tuple[int, int, str]]:
    return (
        numeric_date_spans(message, formats)
        + natural_date_spans(message, today)
        + relative_date_spans(message, today)
    )
