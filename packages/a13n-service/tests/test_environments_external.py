"""External envd targets refused before any dial: the endpoint policy and the tombstone. The tests that reach a
daemon are in `test_environments_envd.py`.
"""

from types import SimpleNamespace

import pytest
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.environments import external
from a13n_service.runs.environments.adapters import construct
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.settings import Settings

from .environments_support import EXTERNAL_TOKEN as TOKEN
from .environments_support import change, details, register, stored, target

pytestmark = pytest.mark.anyio


async def insert(service: SimpleNamespace, endpoint: str) -> str:
    """A target recorded as registration records one, without reaching a daemon."""
    tenant = service.tenant
    environment_id = new_object_id("env")
    async with transaction(service.runtime.storage) as session:
        row = EnvironmentRow(
            id=environment_id,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            device_id="laptop",
            endpoint=endpoint,
            owner_principal_id=tenant.principal_id,
            name="laptop",
            status="ready",
            generation=0,
            created_by_id=tenant.principal_id,
        )
        row.token = external.seal(service.runtime.keys, row, TOKEN)
        session.add(row)
    return environment_id


async def test_an_endpoint_outside_the_operator_policy_is_never_dialed(  # type: ignore[no-untyped-def]
    serve, settings: Settings
) -> None:
    providers = settings.providers.model_copy(update={"require_https": True, "http_origins": ()})
    async with serve(settings=settings.model_copy(update={"providers": providers})) as service:
        # The envd schema permits loopback HTTP, but the operator has not allowed this origin.
        denied = await register(service, "http://127.0.0.1:9")
        # Registering again cannot succeed until the endpoint changes, so the refusal is no retryable 503.
        assert denied.status_code == 409, denied.text
        assert details(denied) == {"field": "endpoint", "reason": "provider_endpoint_denied"}
        plaintext = await register(service, "http://envd.example")
        assert plaintext.status_code == 400
        assert {"field": "body.ExternalTargetCreate.endpoint", "reason": "value_error"} in details(plaintext)["fields"]
        assert (await service.client.get(f"{service.api}/environments")).json()["items"] == []

        # A target whose endpoint the policy now denies is refused at its next dial and at re-verification.
        environment_id = await insert(service, "http://127.0.0.1:9")
        with pytest.raises(EnvironmentProviderError) as refused:
            await construct(
                service.runtime, await target(service, environment_id), operation_id=None, allow_create=False
            )
        assert refused.value.code == "provider_endpoint_denied"
        changed = await change(service, environment_id, {"token": "another-token"})
        assert changed.status_code == 409 and details(changed)["reason"] == "provider_endpoint_denied"


async def test_deleting_a_target_keeps_its_device_and_drops_its_token(service) -> None:  # type: ignore[no-untyped-def]
    environment_id = await insert(service, "https://laptop.test")
    renamed = await change(service, environment_id, {"name": "Laptop"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Laptop"

    path = f"{service.api}/environments/{environment_id}"
    current = await service.client.get(path)
    deleted = await service.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert deleted.status_code == 202 and deleted.json()["status"] == "deleted"
    assert (deleted.json()["device_id"], deleted.json()["endpoint"]) == ("laptop", "https://laptop.test")
    row = await stored(service, environment_id)
    assert (row.token, row.handle, row.device_id) == (None, None, "laptop")
    gone = await change(service, environment_id, {"token": TOKEN})
    assert gone.status_code == 409 and details(gone)["reason"] == "environment_deleted"

    # Only an external target has an endpoint and token.
    provider = await service.client.post(f"{service.api}/environment-providers", json={"type": "docker", "name": "D"})
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={"name": "Box", "provider_id": provider.json()["id"]},
    )
    managed = await service.client.post(f"{service.api}/environments", json={"template_id": template.json()["id"]})
    assert managed.status_code == 201, managed.text
    for body, field in (({"endpoint": "https://laptop.test", "token": TOKEN}, "endpoint"), ({"token": TOKEN}, "token")):
        refused = await change(service, managed.json()["id"], body)
        assert refused.status_code == 400 and details(refused)["field"] == field
