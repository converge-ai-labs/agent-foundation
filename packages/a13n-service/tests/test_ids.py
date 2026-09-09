import re

import pytest
from a13n_service.ids import ObjectId, new_object_id
from pydantic import TypeAdapter, ValidationError


@pytest.mark.parametrize(
    ("kind", "length"),
    [
        ("ws", 20),
        ("ap", 20),
        ("sess", 24),
        ("apr", 24),
        ("run", 28),
        ("rat", 28),
        ("lev", 32),
        ("opg", 32),
        ("csa", 32),
        ("key", 32),
        ("future", 32),
    ],
)
def test_object_ids_use_hex_with_volume_and_security_budgets(kind: str, length: int) -> None:
    first = new_object_id(kind)
    second = new_object_id(kind)

    assert first != second
    assert re.fullmatch(rf"{kind}_[0-9a-f]{{{length}}}", first)
    assert TypeAdapter(ObjectId).validate_python(first) == first


def test_existing_alphanumeric_identity_is_preserved() -> None:
    existing = "ws_qq41imeeis72jc4nzwfj8xyh"
    assert TypeAdapter(ObjectId).validate_python(existing) == existing


@pytest.mark.parametrize("value", ["ws_1234", "ws_" + "A" * 20, "ws_" + "a" * 65])
def test_object_id_acceptance_still_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ObjectId).validate_python(value)


@pytest.mark.parametrize("kind", ["Connector", "w", "workspace", "ws_", "1ws", "ws\n"])
def test_object_id_allocation_rejects_invalid_kind(kind: str) -> None:
    with pytest.raises(ValueError, match="kind"):
        new_object_id(kind)
