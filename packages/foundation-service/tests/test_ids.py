import pytest
from a13n_service.ids import new_object_id, validate_object_id


def test_object_ids_are_kind_prefixed_unpredictable_values() -> None:
    first = new_object_id("con")
    second = new_object_id("con")

    assert first != second
    assert validate_object_id(first, prefix="con") == first
    assert first.startswith("con_")


def test_object_id_validation_rejects_wrong_kind_and_invalid_prefix() -> None:
    with pytest.raises(ValueError, match="invalid Foundation object ID"):
        validate_object_id(new_object_id("con"), prefix="conn")
    with pytest.raises(ValueError, match="prefix"):
        new_object_id("Connector")
