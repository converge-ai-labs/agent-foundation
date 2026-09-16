import json

import pytest
from a13n_service.agent_configuration.resources import ConfigurationResources, safe_schema
from a13n_service.models.models import ModelProviderRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction

from ..agents.conftest import NOW, ORG_ID, PROVIDER_ID, SECRET_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def test_resource_projections_never_serialize_provider_configuration_or_other_users_secrets(agent_sessions):
    async with transaction(agent_sessions) as session:
        provider = await session.get(ModelProviderRecord, PROVIDER_ID)
        provider.configuration = {"api_key": "private-provider-value", "base_url": "https://secret@example.com"}
        session.add(
            SecretRecord(
                id="sec_otheruser1234567",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="user",
                owner_id="usr_anotherperson1234",
                key="other-private-key",
                version=1,
                ciphertext=b"private-ciphertext",
                nonce=b"123456789012",
                encryption_key_id="private-key-id",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    resources = ConfigurationResources(agent_sessions)
    provider = await resources.get(actor=actor(), kind="model_provider", resource_id=PROVIDER_ID)
    encoded = provider.model_dump_json()
    assert "private-provider-value" not in encoded and "base_url" not in encoded and "configuration" not in encoded
    secrets = await resources.search(actor=actor(), kind="secret")
    assert [item.id for item in secrets.items] == [SECRET_ID]
    assert "ciphertext" not in secrets.model_dump_json() and "other-private-key" not in secrets.model_dump_json()


async def test_schema_free_text_and_defaults_cannot_smuggle_credentials():
    schema = {
        "type": "object",
        "description": "secret-description",
        "default": "secret-default",
        "examples": ["secret-example"],
        "properties": {"input": {"type": "string", "default": "nested-secret"}},
        "required": ["input"],
        "additionalProperties": False,
    }
    projected = safe_schema(schema)
    assert projected == {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
        "additionalProperties": False,
    }
    assert "secret" not in json.dumps(projected)
