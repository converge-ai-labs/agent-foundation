"""Native E2B secrets survive encrypted persistence and credential rotation."""

import json

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_environment.e2b.configuration import E2BCredential
from a13n_service.environments.domain import CreateProviderRequest, ReplaceCredentialRequest
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.etags import resource_etag
from a13n_service.storage import short_session

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def provider_catalog():
    return build_environment_provider_catalog(builtin_keys=("a13n.e2b",))


async def test_e2b_secret_survives_creation_and_rotation(environment_service, environment_sessions, protector):
    key = "e2b-test-initial"
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.e2b", name="E2B credential test", credential={"api_key": key}),
    )
    for generation in (1, 2):
        assert provider.credential_configured and key not in provider.model_dump_json()
        async with short_session(environment_sessions) as session:
            row = await session.get(EnvironmentProviderRecord, provider.id)
            assert row.credential_generation == generation
            assert key.encode() not in row.ciphertext
            plaintext = row.credential_snapshot().decrypt(protector)
        credential = E2BCredential.model_validate_json(plaintext)
        assert credential.api_key.get_secret_value() == key
        assert key not in repr(credential) and key not in credential.model_dump_json()
        assert json.loads(plaintext) == {"api_key": key}
        if generation == 1:
            key = "e2b-test-rotated"
            provider = await environment_service.replace_credential(
                actor=actor(),
                provider_id=provider.id,
                if_match=resource_etag(provider.id, provider.updated_at),
                request=ReplaceCredentialRequest(credential={"api_key": key}),
            )
