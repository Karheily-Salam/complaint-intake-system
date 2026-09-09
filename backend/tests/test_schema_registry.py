"""The complaint schema registry is the single source of truth for fields."""

import re
from pathlib import Path

from app.domain.complaint_schemas.registry import get_registry
from app.domain.complaint_schemas.spec import ComplaintSchema

_DEPOSIT_YAML = (
    Path(__file__).resolve().parents[1]
    / "app/domain/complaint_schemas/definitions/deposit.yaml"
)


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


def test_deposit_has_only_generic_flat_fields():
    dep = get_registry().get("deposit")
    assert dep.required_keys() == [
        "user_id",
        "account_email",
        "source_wallet_or_account",
        "transaction_date",
        "deposit_method",
        "problem_description",
    ]
    # fields_for() takes no method argument and returns a flat list
    assert [f.key for f in dep.fields_for()] == dep.required_keys()


def test_deposit_method_is_a_plain_string_field():
    dep = get_registry().get("deposit")
    method = next(f for f in dep.fields_for() if f.key == "deposit_method")
    assert method.type == "string"
    assert method.required is True
    assert method.validation.enum_values is None  # not an enum / not a fixed list


def test_no_method_branching_capability_on_the_schema():
    dep = get_registry().get("deposit")
    for attr in ("methods", "method_field", "method_keys", "method_aliases"):
        assert not hasattr(dep, attr), f"deposit schema still exposes '{attr}'"
    assert set(ComplaintSchema.model_fields) == {
        "type",
        "label",
        "description",
        "open_schema",
        "min_description_words",
        "common_fields",
    }


def test_deposit_yaml_contains_no_hardcoded_methods():
    text = _DEPOSIT_YAML.read_text(encoding="utf-8")
    # no method-branching structure
    assert not re.search(r"(?m)^(methods|method_field):", text)
    assert "additional_fields" not in text
    assert not re.search(r"(?mi)^\s*aliases:", text)
    # no predefined method keys as list entries
    assert not re.search(r"(?mi)-\s*key:\s*(bank_transfer|crypto|e_wallet|card_)\b", text)


def test_other_is_open_schema():
    other = get_registry().get("other")
    assert other.open_schema is True
    assert other.min_description_words >= 1
