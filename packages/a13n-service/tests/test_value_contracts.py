"""Cross-feature validation boundaries that must agree with persistence."""

import hashlib
import json

import pytest
from a13n_service.agents.domain import PluginKey as SelectedPluginKey
from a13n_service.assets.domain import normalize_media_type
from a13n_service.digests import digest_request
from a13n_service.ids import ObjectId
from a13n_service.plugins.artifact import _parse_entry_points
from a13n_service.plugins.domain import PluginKey
from pydantic import TypeAdapter, ValidationError


def test_object_ids_fit_the_relational_identifier_width() -> None:
    validate = TypeAdapter(ObjectId).validate_python
    assert validate("abcdefgh_" + "a" * 63)
    with pytest.raises(ValidationError):
        validate("abcdefgh_" + "a" * 64)


@pytest.mark.parametrize("key", ["foo", "foo.bar", "a_", "a" * 128])
def test_plugin_admission_and_agent_selection_accept_the_same_keys(key: str) -> None:
    assert TypeAdapter(PluginKey).validate_python(key) == TypeAdapter(SelectedPluginKey).validate_python(key)
    assert _parse_entry_points(f"[a13n_harness.plugins]\n{key} = plugin:factory\n".encode())[0] == key


@pytest.mark.parametrize("key", ["1foo.bar", "a", "Foo.bar", "a" * 129])
def test_plugin_selection_rejects_keys_that_cannot_be_uploaded(key: str) -> None:
    for annotation in (PluginKey, SelectedPluginKey):
        with pytest.raises(ValidationError):
            TypeAdapter(annotation).validate_python(key)


def test_media_type_bound_applies_after_defaulting_to_all_callers() -> None:
    assert normalize_media_type("a/" + "B" * 253) == "a/" + "b" * 253
    with pytest.raises(ValueError):
        normalize_media_type("a/" + "b" * 254)


def test_ordinary_request_digest_keeps_existing_unicode_byte_encoding() -> None:
    value = {"name": "Straße", "number": 3}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert digest_request(value) == hashlib.sha256(encoded).hexdigest()


def test_system_attribution_is_not_an_authenticatable_principal() -> None:
    from a13n_service.iam.domain import ActorRef, PrincipalRef

    actor = {"principal_type": "system", "principal_id": "sys_1234567890abcdef"}
    assert TypeAdapter(ActorRef).validate_python(actor).model_dump(mode="json") == actor
    with pytest.raises(ValidationError):
        PrincipalRef.model_validate(actor)
