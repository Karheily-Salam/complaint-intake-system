"""Strip quoted reply history from an inbound email body.

Real mail clients append the entire previous exchange to every reply. Feeding
that to classification and extraction is actively harmful, not merely noisy:
production showed a customer answering "89669207061" to a request for the
transaction date, and the date parser reading "Sep 11, 2026" out of Gmail's
own quoted attribution line instead - producing a confident, valid-looking,
completely wrong value.

This module is deliberately:

* **pure and deterministic** - same input, same output, no clock, no I/O, so
  it can be tested directly and its behaviour never depends on when it runs;
* **conservative** - it would rather leave quoted text in than eat a sentence
  of a real complaint. Every cut point has to look unambiguously like a mail
  client's own scaffolding, and if stripping would empty the message the
  original is returned untouched;
* **advisory** - the raw body is still what gets persisted. Only the copy
  handed to the AI layer is cleaned (see IntakeService._run_turn).
"""

from __future__ import annotations

import re

# Lines that a mail client inserts above the text it is quoting. Each is
# anchored to the END of the line, which is what keeps prose safe: a customer
# writing "On Friday I wrote a letter to your support team" never matches,
# because that line does not *end* at "wrote:".
_ATTRIBUTION_TAIL = re.compile(
    r"(?:"
    r"wrote\s*:"                       # en: On <date> <name> wrote:
    r"|sent\s+from\s+my\s+\w+"         # en: mobile signatures used as a divider
    r"|писа(?:л|ла|л\(а\))\s*:"        # ru: <date> <name> писал(а):
    r"|пишет\s*:"                      # ru: <name> пишет:
    r"|كتب\s*:"                        # ar: في <date> <name> كتب:
    r"|كتبت\s*:"                       # ar: feminine form
    r")\s*$",
    re.IGNORECASE,
)

# The opening token of an attribution line. Requiring this *and* the tail
# above is what stops a stray "...wrote:" inside a genuine complaint from
# truncating the message.
_ATTRIBUTION_HEAD = re.compile(
    r"^\s*(?:On\b|В\b|في\b|بتاريخ\b|Am\b|Le\b|El\b)",
    re.IGNORECASE,
)

# Russian and several other clients lead with the date instead of a word
# ("11 сентября 2026 г., 18:54 Имя <a@b> писал(а):"), so the head above never
# matches them. Such a line still counts as an attribution when it carries the
# scaffolding a mail client puts there - an angle-bracketed address or a
# clock time - which ordinary complaint prose ending in "wrote:" does not.
_ATTRIBUTION_CONTEXT = re.compile(r"<[^>\s]*@[^>\s]*>|\b\d{1,2}:\d{2}\b")

# Separators that are unambiguous on their own - no head/tail pairing needed.
_HARD_SEPARATORS = (
    re.compile(r"^\s*-{2,}\s*original\s+message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*forwarded\s+message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*исходное\s+сообщение\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*الرسالة\s+الأصلية\s*-{2,}\s*$"),
    re.compile(r"^_{10,}\s*$"),          # Outlook's horizontal rule above a quote
    re.compile(r"^\s*-{5,}\s*$"),        # long dashed rule used by several clients
)

# An Outlook-style quoted header block. "From:" alone is too weak - a customer
# may legitimately write it - so it only counts when the following lines carry
# the rest of the block.
_OUTLOOK_FROM = re.compile(r"^\s*(?:From|От|من)\s*:\s*\S", re.IGNORECASE)
_OUTLOOK_FOLLOW = re.compile(
    r"^\s*(?:Sent|Date|To|Subject|Отправлено|Дата|Кому|Тема|إلى|التاريخ|الموضوع)\s*:",
    re.IGNORECASE,
)

# RFC 3676 signature delimiter: a line of exactly "--" (conventionally with a
# trailing space). Only honoured when real content precedes it.
_SIGNATURE_DELIM = re.compile(r"^--\s*$")

_QUOTED_LINE = re.compile(r"^\s*>")

# How far the wrapped tail of an attribution line may sit from its head.
# Gmail folds "On <date> <name> <addr>" and puts a bare "wrote:" on the next
# line, so a one-line regex misses the single most common case in production.
_ATTRIBUTION_LOOKAHEAD = 2


def _attribution_cut(lines: list[str]) -> int | None:
    """Index of the first line that begins a quoted-reply attribution."""
    for i, line in enumerate(lines):
        if _ATTRIBUTION_HEAD.match(line):
            # A headed attribution may be folded across lines, so look ahead -
            # but the window still has to *start* here, or the customer's own
            # text above would be swept in with it.
            for extra in range(_ATTRIBUTION_LOOKAHEAD + 1):
                window = " ".join(lines[i : i + extra + 1]).strip()
                if _ATTRIBUTION_TAIL.search(window):
                    return i
        elif _ATTRIBUTION_CONTEXT.search(line) and _ATTRIBUTION_TAIL.search(line.strip()):
            # Date-led attribution (Russian and others): no leading keyword, so
            # it is only recognised when the marker and the mail-client
            # scaffolding are on this one line. No lookahead here - that would
            # be too easy to trigger from ordinary prose.
            return i
    return None


def _hard_cut(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if any(sep.match(line) for sep in _HARD_SEPARATORS):
            return i
        if _OUTLOOK_FROM.match(line) and any(
            _OUTLOOK_FOLLOW.match(nxt) for nxt in lines[i + 1 : i + 4]
        ):
            return i
    return None


def _signature_cut(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _SIGNATURE_DELIM.match(line) and any(ln.strip() for ln in lines[:i]):
            return i
    return None


def _tidy(text: str) -> str:
    """Collapse the blank lines a cut leaves behind, without touching words."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_quoted_reply(body: str) -> str:
    """Return ``body`` with quoted history and signatures removed.

    Falls back to the original whenever stripping would leave nothing - an
    empty message would make the engine ask again for something the customer
    already answered, which is worse than a little leftover quoting.
    """
    if not body or not body.strip():
        return body

    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Take the earliest cut any rule proposes: quoting only ever appears below
    # the customer's own new text.
    cuts = [c for c in (_attribution_cut(lines), _hard_cut(lines), _signature_cut(lines))
            if c is not None]
    if cuts:
        lines = lines[: min(cuts)]

    # Whatever survives may still contain ">" quoting (a reply interleaved
    # above the attribution, or a client that quotes without one).
    kept = [ln for ln in lines if not _QUOTED_LINE.match(ln)]

    cleaned = _tidy("\n".join(kept))
    if not cleaned:
        # Everything looked like quoting. Trust the original over an empty
        # string; the caller needs *something* to work with.
        return _tidy(body) or body
    return cleaned
