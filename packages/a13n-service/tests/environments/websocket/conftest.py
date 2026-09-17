from __future__ import annotations

import pytest
from a13n_environment import EnvironmentState, build_environment_provider_catalog
from a13n_service.environments.domain import CreateProviderRequest, RegisterEnvironmentRequest
from a13n_service.environments.websocket.resources import ConnectionResources

from ..conftest import WORKSPACE_ID, actor


@pytest.fixture
def provider_catalog():
    return build_environment_provider_catalog(builtin_keys=("a13n.websocket-envd",))


@pytest.fixture
async def target(environment_service):
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.websocket-envd", name="Client"),
    )
    environment = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="register-client",
        request=RegisterEnvironmentRequest(
            provider_id=provider.id,
            configuration={},
            state=EnvironmentState(
                provider_key="a13n.websocket-envd", state_version="1", state={"daemon_environment_id": "local-computer"}
            ),
        ),
    )
    return await ConnectionResources(environment_service).authorized(actor(), environment.id, manage=True)
