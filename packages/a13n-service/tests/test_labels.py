import pytest
from a13n_service.labels import Labels, LabelsBody, labels_etag, merge_labels, parse_label_filters
from pydantic import TypeAdapter, ValidationError


def test_labels_are_strict_and_canonical() -> None:
    labels = TypeAdapter(Labels).validate_python({"z": " spaced ", "A.1": "值"})
    assert labels == {"A.1": "值", "z": " spaced "}


@pytest.mark.parametrize("key", ["", "_private", "has space", "x" * 64, "é"])
def test_invalid_label_key_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Labels).validate_python({key: "value"})


@pytest.mark.parametrize("value", ["\n", "x" * 257, "\ud800"])
def test_invalid_label_value_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Labels).validate_python({"key": value})


@pytest.mark.parametrize("value", [1, True, None, ["value"]])
def test_non_string_label_value_is_not_coerced(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Labels).validate_python({"key": value})


def test_labels_body_requires_an_explicit_map() -> None:
    with pytest.raises(ValidationError):
        LabelsBody.model_validate({})
    assert LabelsBody.model_validate({"labels": {}}).labels == {}


def test_merge_validates_final_size() -> None:
    source = {f"k{index}": "v" for index in range(32)}
    assert merge_labels(source, {"k0": "new"})["k0"] == "new"
    with pytest.raises(ValueError, match="at most 32"):
        merge_labels(source, {"extra": "v"})


def test_label_etag_is_order_independent_and_label_specific() -> None:
    assert labels_etag("r1", {"b": "2", "a": "1"}) == labels_etag("r1", {"a": "1", "b": "2"})
    assert labels_etag("r1", {"a": "1"}) != labels_etag("r1", {"a": "2"})


def test_filters_deduplicate_and_reject_conflicts() -> None:
    assert parse_label_filters(("batch=eval-09", "batch=eval-09", "kind=test")) == {
        "batch": "eval-09",
        "kind": "test",
    }
    with pytest.raises(ValueError, match="conflicting"):
        parse_label_filters(("batch=one", "batch=two"))
    with pytest.raises(ValueError, match="at most 32"):
        parse_label_filters(tuple(f"key{index}=value" for index in range(33)))
    with pytest.raises(ValueError, match="at most 64"):
        parse_label_filters(("key=value",) * 65)
