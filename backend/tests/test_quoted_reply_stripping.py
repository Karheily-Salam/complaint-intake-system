"""Quoted reply history must not reach classification or extraction.

Production proved the cost of not doing this: a customer answered
"89669207061" when asked for the transaction date, and the date parser read
"Sep 11, 2026" out of Gmail's own attribution line instead - storing a
confident, valid-looking, wrong date on a real ticket.

The stripper is deliberately conservative, so roughly half of these tests
check the opposite direction: that ordinary complaint prose containing the
words "wrote", "On Friday" or a colon is left completely alone.
"""

from __future__ import annotations

import pytest

from app.email.quoting import strip_quoted_reply

# The message that actually caused the production defect, verbatim.
PRODUCTION_CASE = (
    "89669207061\r\n"
    "\r\n"
    "On Fri, Sep 11, 2026 at 18:54 A Sender <sender@example.com>\r\n"
    "wrote:\r\n"
    "\r\n"
    "> yes\r\n"
)


def test_the_production_message_is_reduced_to_the_customers_answer():
    assert strip_quoted_reply(PRODUCTION_CASE) == "89669207061"


def test_no_quoted_date_survives_to_be_misread():
    cleaned = strip_quoted_reply(PRODUCTION_CASE)
    for fragment in ("Sep", "2026", "wrote", "sender@example.com"):
        assert fragment not in cleaned


# ------------------------------------------------------------------ clients


def test_gmail_folded_attribution():
    """Gmail wraps the attribution, leaving a bare 'wrote:' on its own line."""
    body = (
        "My withdrawal never arrived.\n\n"
        "On Fri, Sep 11, 2026 at 18:54 Support <support@example.com>\n"
        "wrote:\n\n"
        "> Please provide your user ID.\n"
    )
    assert strip_quoted_reply(body) == "My withdrawal never arrived."


def test_gmail_single_line_attribution():
    body = (
        "U-482913\n\n"
        "On Mon, 1 Jan 2026 at 09:00, Support <s@example.com> wrote:\n"
        "> What is your user id?\n"
    )
    assert strip_quoted_reply(body) == "U-482913"


def test_outlook_original_message_separator():
    body = (
        "The amount is wrong.\n\n"
        "-----Original Message-----\n"
        "From: Support <s@example.com>\n"
        "Sent: Monday\n"
        "Subject: Re: deposit\n"
        "Everything below is history.\n"
    )
    assert strip_quoted_reply(body) == "The amount is wrong."


def test_outlook_header_block_without_separator():
    body = (
        "Still not resolved.\n\n"
        "From: Support <s@example.com>\n"
        "Sent: 11 September 2026 18:54\n"
        "To: me@example.com\n"
        "Subject: Re: deposit\n\n"
        "old text\n"
    )
    assert strip_quoted_reply(body) == "Still not resolved."


def test_outlook_underscore_rule():
    body = "TXN-9f3a12bc\n\n" + "_" * 32 + "\nFrom: Support\nolder text\n"
    assert strip_quoted_reply(body) == "TXN-9f3a12bc"


def test_plain_quoted_lines_without_any_attribution():
    body = "Here is my id: U-1\n> previous question\n> more quoted\n"
    assert strip_quoted_reply(body) == "Here is my id: U-1"


def test_nested_quotes_are_removed():
    body = (
        "1111\n\n"
        "On Fri, 11 Sep 2026, Support wrote:\n"
        "> On Thu, 10 Sep 2026, Customer wrote:\n"
        ">> original complaint text\n"
        "> reply text\n"
    )
    assert strip_quoted_reply(body) == "1111"


def test_signature_after_rfc_delimiter_is_dropped():
    body = "My deposit is missing.\n\n--\nJane Doe\nSenior Analyst\n+1 555 0100\n"
    assert strip_quoted_reply(body) == "My deposit is missing."


def test_signature_delimiter_at_the_very_start_is_not_a_cut():
    """Nothing precedes it, so treating it as a signature would empty the message."""
    body = "--\nstill the only content\n"
    assert "still the only content" in strip_quoted_reply(body)


# ---------------------------------------------------------------- languages


def test_russian_attribution():
    body = (
        "Мой вывод средств не пришёл.\n\n"
        "11 сентября 2026 г., 18:54 Поддержка <s@example.com> писал(а):\n"
        "> Укажите ваш ID пользователя.\n"
    )
    assert strip_quoted_reply(body) == "Мой вывод средств не пришёл."


def test_russian_original_message_separator():
    body = "Деньги не поступили.\n\n-----Исходное сообщение-----\nОт: Поддержка\nстарый текст\n"
    assert strip_quoted_reply(body) == "Деньги не поступили."


def test_arabic_attribution():
    body = (
        "لم يصلني مبلغ السحب.\n\n"
        "في الجمعة، ١١ سبتمبر ٢٠٢٦، الدعم <s@example.com> كتب:\n"
        "> يرجى تزويدنا برقم المستخدم.\n"
    )
    assert strip_quoted_reply(body) == "لم يصلني مبلغ السحب."


def test_arabic_reply_keeps_only_the_new_text():
    body = (
        "تحويل\r\n\r\n"
        "في ١١ سبتمبر ٢٠٢٦ <complaints@startplus.tech> كتب:\r\n\r\n"
        "> يرجى إخبارنا\r\n"
    )
    assert strip_quoted_reply(body) == "تحويل"


# -------------------------------------------------------------- adversarial


@pytest.mark.parametrize(
    "text",
    [
        "On Friday I wrote a letter to your support team and nobody replied.",
        "I wrote: please refund me. Nobody answered.",
        "On Monday my deposit vanished and on Tuesday it came back.",
        "The bank wrote to me saying the transfer was fine.",
        "On 3 August I sent 500 EUR and it never arrived.",
        "Subject: my complaint is that nothing works",
        "From what I can tell, the money left my account.",
        "В пятницу я написал в банк, но ответа не было.",
        "في يوم الجمعة كتبت رسالة إلى الدعم ولم يرد أحد.",
    ],
)
def test_legitimate_prose_is_never_truncated(text):
    assert strip_quoted_reply(text) == text


def test_a_message_that_is_entirely_quoted_falls_back_to_the_original():
    """An empty result would make the engine re-ask for what was just answered."""
    body = "> only quoted content here\n> and nothing else\n"
    assert strip_quoted_reply(body).strip() != ""


def test_empty_and_whitespace_input_is_returned_unchanged():
    assert strip_quoted_reply("") == ""
    assert strip_quoted_reply("   \n  ") == "   \n  "


def test_is_deterministic_and_idempotent():
    once = strip_quoted_reply(PRODUCTION_CASE)
    assert once == strip_quoted_reply(PRODUCTION_CASE)
    assert strip_quoted_reply(once) == once


def test_multiline_new_text_above_the_quote_is_kept_whole():
    body = (
        "I made a deposit on 8 September.\n"
        "It still has not been credited.\n"
        "Please help.\n\n"
        "On Fri, 11 Sep 2026 at 10:00, Support <s@example.com> wrote:\n"
        "> We are looking into it.\n"
    )
    cleaned = strip_quoted_reply(body)
    assert cleaned == (
        "I made a deposit on 8 September.\nIt still has not been credited.\nPlease help."
    )


def test_earliest_cut_wins_when_several_markers_appear():
    body = (
        "new text\n\n"
        "On Fri, 11 Sep 2026, Support wrote:\n"
        "> quoted\n\n"
        "--\nSignature\n"
    )
    assert strip_quoted_reply(body) == "new text"
