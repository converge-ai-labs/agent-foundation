"""Native E2B secrets survive encrypted persistence and credential rotation."""

import json

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.e2b.configuration import E2BCredential
from a13n_service.environments.domain import CreateProviderRequest, ReplaceCredentialRequest, UpdateProviderRequest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.etags import resource_etag
from a13n_service.storage import short_session

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def provider_catalog():
    return ProviderCatalog(select_builtin_environment_providers(("e2b",)))


async def test_e2b_secret_survives_creation_and_rotation(environment_service, environment_sessions, protector):
    key = "e2b-test-initial"
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="e2b", name="E2B credential test", credential={"api_key": key}),
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


async def test_provider_update_rolls_back_invalid_credentials(environment_service):
    original = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="e2b", name="Original", credential={"api_key": "initial"}),
    )
    etag = resource_etag(original.id, original.updated_at)
    with pytest.raises(EnvironmentManagementError):
        await environment_service.update_provider(
            actor=actor(),
            provider_id=original.id,
            if_match=etag,
            request=UpdateProviderRequest(name="Renamed", enabled=False, credential={"unknown": "invalid"}),
        )
    assert await environment_service.get_provider(actor=actor(), resource_id=original.id) == original
    updated = await environment_service.update_provider(
        actor=actor(),
        provider_id=original.id,
        if_match=etag,
        request=UpdateProviderRequest(name="Renamed", enabled=False, credential={"api_key": "rotated"}),
    )
    assert updated.name == "Renamed" and not updated.enabled and updated.credential_configured
    assert updated.updated_at != original.updated_at
