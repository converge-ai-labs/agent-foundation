"""Remote providers use existing external registration and runtime construction."""

import pytest
from a13n_environment import EnvironmentState, build_environment_provider_catalog
from a13n_service.environments.domain import (
    CreateProviderRequest,
    CreateTemplateRequest,
    EnvironmentConfiguration,
    RegisterEnvironmentRequest,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.lifecycle import EnvironmentLifecycle, LifecycleOperation
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.storage import short_session

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def provider_catalog():
    return build_environment_provider_catalog(builtin_keys=("a13n.http-envd", "a13n.websocket-envd"))


async def test_http_registration_runtime_and_external_only_metadata(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path
):
    service = environment_service
    types = await service.provider_types(actor())
    assert {item.type for item in types.items} == {"a13n.http-envd", "a13n.websocket-envd"}
    assert {item.type: item.display_name for item in types.items} == {
        "a13n.http-envd": "HTTP Envd",
        "a13n.websocket-envd": "WebSocket Envd",
    }
    assert all(not item.supports_managed for item in types.items)
    provider = await service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(
            type="a13n.http-envd",
            name="External daemon",
            configuration={"endpoint": "https://envd.example"},
            credential={"token": "private-example-token"},
        ),
    )
    assert provider.credential_configured
    assert "private-example-token" not in provider.model_dump_json()
    with pytest.raises(EnvironmentManagementError, match="external registration only"):
        await service.create_template(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="invalid-template",
            request=CreateTemplateRequest(
                name="Invalid",
                provider_id=provider.id,
                configuration={},
                retention={"idle": {"stop_after": None, "delete_after": None}},
            ),
        )
    selected_state = EnvironmentState(
        provider_key="a13n.http-envd", state_version="1", state={"daemon_environment_id": "env-native"}
    )
    environment = await service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="external",
        request=RegisterEnvironmentRequest(provider_id=provider.id, configuration={}, state=selected_state),
    )
    assert environment.ownership == "external"
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path)
    # Construct the worker's immutable runtime input from a closed short read.
    # This checks reconstruction only; actual preparation requires RunAttempt authority.
    async with short_session(environment_sessions) as session:
        record = await session.get(EnvironmentProviderRecord, provider.id)
        operation = LifecycleOperation(
            environment_id=environment.id,
            provider_type=record.type,
            provider_configuration=record.configuration,
            credential=record.credential_snapshot(),
            configuration=EnvironmentConfiguration(configuration={}),
            state=selected_state,
            operation_id="operation-test",
            fence=1,
            owner="owner-test",
            action="prepare",
            previous_status="unprepared",
        )
    adapter = await lifecycle.construct(operation)
    try:
        assert adapter.environment_id == environment.id
        assert adapter.dump_state() == selected_state
        assert adapter.descriptor.generation == "unprepared"
        assert adapter.operations.files is None
    finally:
        await adapter.close()


async def test_remote_registration_requires_exact_state_without_network(environment_service):
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(
            type="a13n.http-envd",
            name="External",
            configuration={"endpoint": "https://envd.example"},
            credential={"token": "test-token"},
        ),
    )
    with pytest.raises(EnvironmentManagementError, match="state is invalid"):
        await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="invalid-external",
            request=RegisterEnvironmentRequest(provider_id=provider.id, configuration={}),
        )


async def test_connection_tuning_cannot_register_the_same_target_twice(environment_service):
    providers = []
    for timeout in (10, 20):
        providers.append(
            await environment_service.create_provider(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                request=CreateProviderRequest(
                    type="a13n.http-envd",
                    name=f"External {timeout}",
                    configuration={"endpoint": "https://envd.example", "request_timeout": timeout},
                    credential={"token": "test-token"},
                ),
            )
        )
    state = EnvironmentState(
        provider_key="a13n.http-envd", state_version="1", state={"daemon_environment_id": "env-native"}
    )
    await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="first-owner",
        request=RegisterEnvironmentRequest(provider_id=providers[0].id, configuration={}, state=state),
    )
    with pytest.raises(EnvironmentManagementError, match="already"):
        await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="duplicate-owner",
            request=RegisterEnvironmentRequest(provider_id=providers[1].id, configuration={}, state=state),
        )
