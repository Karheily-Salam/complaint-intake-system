"""An invalid date answer must stay invalid - never today's date.

Production stored `transaction_date = 2026-09-11` on a real ticket after the
customer answered "89669207061". Nothing rejected it, because the value the
extractor produced *was* a valid date - it had simply been read out of the
quoted Gmail attribution line, with the year supplied by the clock.

These tests pin the two halves of that failure separately, so a regression in
either one is visible on its own.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ai.providers.rule_based import RuleBasedAIProvider, _parse_natural_date
from app.domain.complaint_schemas.registry import get_registry
from app.domain.complaint_schemas.spec import FieldType
from app.domain.validation import validate_field
from app.email.quoting import strip_quoted_reply

PRODUCTION_ANSWER = (
    "89669207061\r\n\r\n"
    "On Fri, Sep 11, 2026 at 18:54 Salam Karheily <salaam.karhiely@gmail.com>\r\n"
    "wrote:\r\n\r\n> yes\r\n"
)


def date_spec():
    spec = next(
        f for f in get_registry().get("deposit").fields_for() if f.key == "transaction_date"
    )
    assert spec.type == FieldType.DATE
    return spec


# ------------------------------------------------- the exact production case


def test_the_production_answer_no_longer_yields_a_date():
    """The whole point: this used to return today's date."""
    cleaned = strip_quoted_reply(PRODUCTION_ANSWER)
    assert _parse_natural_date(cleaned) is None


def test_the_production_answer_fails_validation_rather_than_being_stored():
    result = validate_field(date_spec(), strip_quoted_reply(PRODUCTION_ANSWER).strip() or None)
    assert not result.ok


async def test_extraction_does_not_invent_a_date_for_the_production_answer():
    provider = RuleBasedAIProvider()
    spec = date_spec()
    result = await provider.extract(
        strip_quoted_reply(PRODUCTION_ANSWER),
        [spec],
        known={},
        pending_field="transaction_date",
    )
    assert "transaction_date" not in result.as_dict()


# ------------------------------------------------------- values that are not dates


@pytest.mark.parametrize(
    "answer",
    [
        "89669207061",          # the production answer: a phone number
        "1111",                 # a user id
        "TXN-9f3a12bc",         # a transaction reference
        "753167",               # an account number
        "1757606400",           # a unix timestamp
        "4111111111111111",     # a card-shaped number
        "",
        "   ",
        "yes",
        "не знаю",
        "لا أعرف",
    ],
)
async def test_non_dates_are_never_read_as_a_date(answer):
    assert _parse_natural_date(answer) is None
    provider = RuleBasedAIProvider()
    extracted = await provider.extract(
        answer, [date_spec()], known={}, pending_field="transaction_date"
    )
    assert "transaction_date" not in extracted.as_dict()


@pytest.mark.parametrize("answer", ["89669207061", "1111", "", "yes"])
def test_non_dates_do_not_validate(answer):
    assert not validate_field(date_spec(), answer or None).ok


def test_todays_date_is_never_the_answer_to_a_non_date():
    today = date.today().strftime("%Y-%m-%d")
    for answer in ("89669207061", "1111", "753167", "TXN-9f3a12bc"):
        assert _parse_natural_date(answer) != today


# ------------------------------------------------------------ valid formats


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("2026-09-11", "2026-09-11"),
        ("11.09.2026", "2026-09-11"),
        ("11/09/2026", "2026-09-11"),
    ],
)
async def test_supported_numeric_formats_still_work(answer, expected):
    provider = RuleBasedAIProvider()
    extracted = await provider.extract(
        answer, [date_spec()], known={}, pending_field="transaction_date"
    )
    raw = extracted.as_dict().get("transaction_date")
    assert raw is not None
    result = validate_field(date_spec(), raw)
    assert result.ok and result.normalized_value == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("11 Sep 2026", "2026-09-11"),
        ("Sep 11, 2026", "2026-09-11"),
        ("11 сентября 2026", "2026-09-11"),
        ("١١ سبتمبر 2026", "2026-09-11"),
    ],
)
def test_natural_dates_with_a_stated_year_use_that_year(answer, expected):
    assert _parse_natural_date(answer) == expected


def test_a_stated_past_year_is_honoured_not_replaced_by_the_current_one():
    """This silently stored the wrong year before the fix."""
    assert _parse_natural_date("Sep 11, 2024") == "2024-09-11"
    assert _parse_natural_date("11 сентября 2019") == "2019-09-11"


def test_bare_month_and_day_still_assume_the_current_year():
    """The schema's extraction_hint invites this phrasing; keep supporting it."""
    year = date.today().year
    assert _parse_natural_date("8 September") == f"{year}-09-08"
    assert _parse_natural_date("8 сентября") == f"{year}-09-08"


# -------------------------------------------------- impossible calendar dates


@pytest.mark.parametrize(
    "answer", ["31 February 2026", "31 Feb 2026", "30 February", "32 January 2026"]
)
def test_impossible_calendar_dates_are_rejected(answer):
    assert _parse_natural_date(answer) is None


@pytest.mark.parametrize("answer", ["31.02.2026", "2026-02-31", "32/01/2026"])
def test_impossible_numeric_dates_fail_validation(answer):
    assert not validate_field(date_spec(), answer).ok


def test_a_long_number_beside_a_month_name_is_not_a_day():
    """The day must be a one- or two-digit token."""
    assert _parse_natural_date("89669207061 September") is None
    assert _parse_natural_date("September 89669207061") is None


def test_a_distant_number_is_not_adopted_as_the_day():
    assert _parse_natural_date("September was terrible and my id is 7") is None
