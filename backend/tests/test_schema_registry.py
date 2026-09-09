"""The complaint schema registry is the single source of truth for fields."""

from app.domain.complaint_schemas.registry import get_registry
from app.domain.complaint_schemas.spec import FieldType


def test_all_three_types_load():
    reg = get_registry()
    assert set(reg.types()) == {"withdrawal", "deposit", "other"}


def test_withdrawal_required_fields():
    wd = get_registry().get("withdrawal")
    assert wd.required_keys() == [
        "user_id",
        "account_email",
        "withdrawal_transaction_id",
        "problem_description",
    ]
    assert wd.method_field is None and wd.methods == []


def test_deposit_method_field_is_enum_derived_from_methods():
    dep = get_registry().get("deposit")
    assert dep.method_field is not None
    assert dep.method_field.type == FieldType.ENUM
    assert dep.method_field.validation.enum_values == [
        "bank_transfer",
        "crypto",
        "card",
        "e_wallet",
    ]
    # method_field is always part of the base required set
    assert "deposit_method" in dep.required_keys()


def test_deposit_method_specific_fields_only_appear_with_method():
    dep = get_registry().get("deposit")
    base = set(dep.required_keys())
    with_card = set(dep.required_keys("card"))
    assert base.isdisjoint({"card_last_four", "card_auth_code", "payment_processor_reference"})
    assert {"card_last_four", "card_auth_code", "payment_processor_reference"} <= with_card
    assert base < with_card


def test_deposit_method_aliases_present_for_matching():
    dep = get_registry().get("deposit")
    aliases = dep.method_field.validation.enum_aliases
    assert "paypal" in [a.lower() for a in aliases["e_wallet"]]
    assert "bank transfer" in [a.lower() for a in aliases["bank_transfer"]]


def test_other_is_open_schema():
    other = get_registry().get("other")
    assert other.open_schema is True
    assert other.min_description_words >= 1
