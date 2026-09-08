"""Cross-feature validation boundaries that must agree with persistence."""

import hashlib
import json

import pytest
from a13n_service.agents.domain import PluginSelection
from a13n_service.assets.domain import normalize_media_type
from a13n_service.digests import digest_request
from a13n_service.ids import ObjectId
from a13n_service.settings import Settings
from pydantic import TypeAdapter, ValidationError


def test_object_ids_fit_the_relational_identifier_width() -> None:
    validate = TypeAdapter(ObjectId).validate_python
    assert validate("abcdefgh_" + "a" * 63)
    with pytest.raises(ValidationError):
        validate("abcdefgh_" + "a" * 64)


@pytest.mark.parametrize("key", ["foo", "foo.bar", "a_", "a" * 128])
def test_plugin_key_and_agent_selection_accept_the_same_keys(key: str) -> None:
    assert (
        PluginSelection(instance_name="instance", plugin_key=key).plugin_key
        == Settings(_env_file=None, plugin_keys=(key,)).plugin_keys[0]
    )


@pytest.mark.parametrize("key", ["1foo.bar", "a", "Foo.bar", "a" * 129])
def test_plugin_selection_rejects_invalid_plugin_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        PluginSelection(instance_name="instance", plugin_key=key)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, plugin_keys=(key,))


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
