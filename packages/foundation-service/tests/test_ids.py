import pytest
from a13n_service.ids import new_object_id


def test_object_ids_are_kind_prefixed_unpredictable_values() -> None:
    first = new_object_id("con")
    second = new_object_id("con")

    assert first != second
    assert first.startswith("con_")
    assert len(first) == len("con_") + 24


def test_object_id_allocation_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        new_object_id("Connector")
