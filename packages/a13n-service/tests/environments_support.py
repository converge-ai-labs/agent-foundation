"""The fake environment providers and the helpers the environments tests share."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import httpx2
from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.errors import (
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
    provider_error,
)
from a13n_harness.providers.environment.management import Environment, EnvironmentProviderConfiguration
from a13n_harness.providers.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from a13n_service.distribution import OSS
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import transaction
from a13n_service.providers.registry import Registry
from a13n_service.runs.environments import external
from a13n_service.runs.environments.adapters import Target
from a13n_service.runs.environments.tables import EnvironmentRow
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import func, update


class FakeRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    image: str = "base"


@dataclass
class Backend:
    """The fake provider's world: instances by environment ID and every call that reached it."""

    instances: dict[str, Literal["running", "stopped"]] = field(default_factory=dict)
    # The operation ID of every preparation that reached the backend.
    preparations: list[str] = field(default_factory=list)
    # The next call takes effect, but its answer is lost on the way back.
    lose_response: bool = False
    # Every renewal that reached the backend, by environment ID; the expiry each instance was last renewed to; and
    # what the next renewals raise.
    renewals: list[str] = field(default_factory=list)
    expiries: dict[str, datetime] = field(default_factory=dict)
    renewal_error: Exception | None = None

    def answer(self) -> None:
        if self.lose_response:
            self.lose_response = False
            raise provider_error(
                "fake",
                "provider_response_lost",
                EnvironmentProviderErrorCategory.UNAVAILABLE,
                certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
            )


BACKEND = Backend()
DESCRIPTOR = EnvironmentDescriptor(
    generation="g1",
    working_directory="/work",
    operation_families=frozenset(),
    permissions=EnvironmentPermissionSet(operations=frozenset()),
)


class FakeEnvironment(Environment):
    def __init__(self, environment_id: str, operation_id: str, *, allow_create: bool, state: EnvironmentState | None):
        super().__init__(state)
        self._id, self._operation, self._allow_create = environment_id, operation_id, allow_create

    @property
    def provider_key(self) -> str:
        return "fake"

    @property
    def environment_id(self) -> str:
        return self._id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return DESCRIPTOR

    @property
    def availability(self) -> EnvironmentAvailability:
        return EnvironmentAvailability(status="available")

    @property
    def operations(self) -> EnvironmentOperations:
        return EnvironmentOperations()

    async def _prepare(self, *, mount_id: str) -> None:
        BACKEND.preparations.append(self._operation)
        if BACKEND.instances.get(self._id) != "running":
            if not self._allow_create:
                raise provider_error("fake", "provider_target_missing", EnvironmentProviderErrorCategory.MISSING)
            BACKEND.instances[self._id] = "running"
        self._cache_state(EnvironmentState(provider_key="fake", state_version="1", state={"instance": self._id}))
        BACKEND.answer()

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        return BACKEND.instances.get(self._id, "absent")

    def _require_owner(self) -> None:
        """Like the hosted backends, only an adapter acting as the instance's owner stops or destroys it."""
        if not self._allow_create:
            raise provider_error("fake", "provider_denied", EnvironmentProviderErrorCategory.DENIED)

    async def _stop(self) -> None:
        self._require_owner()
        BACKEND.instances[self._id] = "stopped"
        BACKEND.answer()

    async def _destroy(self) -> None:
        self._require_owner()
        BACKEND.instances.pop(self._id, None)
        BACKEND.answer()

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        """Like the hosted backends: it observes the instance, never starts or creates one."""
        BACKEND.renewals.append(self._id)
        if self._id not in BACKEND.instances:
            raise provider_error("fake", "provider_target_missing", EnvironmentProviderErrorCategory.MISSING)
        if BACKEND.instances[self._id] != "running":
            raise provider_error("fake", "provider_target_stopped", EnvironmentProviderErrorCategory.CONFLICT)
        if BACKEND.renewal_error is not None:
            raise BACKEND.renewal_error
        BACKEND.expiries[self._id] = deadline
        return deadline

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        return None

    async def _close(self) -> None:
        return None


def _construct(
    *, environment_id: str, state: EnvironmentState | None, operation_id: str, allow_create: bool, **_: object
) -> Environment:
    return FakeEnvironment(environment_id, operation_id, allow_create=allow_create, state=state)


FAKE = EnvironmentProviderDefinition(
    type="fake",
    display_name="Fake",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=FakeRecipe,
    construct=_construct,
    describe_environment=lambda recipe: DESCRIPTOR,
    supports_stop=True,
    supports_destroy=True,
)


class FakeCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr


# A type whose sandboxes end unless renewed, like E2B's, reached with an optional credential.
EXPIRING = replace(
    FAKE,
    type="expiring",
    display_name="Expiring",
    requires_keepalive=True,
    credential_model=FakeCredential,
    authentication=Authentication(mode=CredentialMode.optional),
)


async def with_fake_providers(service: SimpleNamespace) -> SimpleNamespace:
    """`service` whose registry also offers the fake providers, with an agent whose default template is the fake one;
    tests drive maintenance and renewal themselves."""
    for task in service.app.state.background:
        if task.get_name() == "control-sweeps":
            task.cancel()
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, FAKE, EXPIRING)))
    service.app.state.runtime = service.runtime
    BACKEND.instances.clear()
    BACKEND.preparations.clear()
    BACKEND.lose_response = False
    BACKEND.renewals.clear()
    BACKEND.expiries.clear()
    BACKEND.renewal_error = None
    client = service.client
    provider = await client.post(f"{service.api}/environment-providers", json={"type": "fake", "name": "Fake"})
    assert provider.status_code == 201, provider.text
    template = await client.post(
        f"{service.api}/environment-templates", json={"name": "Box", "provider_id": provider.json()["id"]}
    )
    assert template.status_code == 201, template.text
    # Runs are only accepted and prepared here, so the model is never called.
    config = {"base_url": "http://127.0.0.1:9/v1"}
    model_provider = await client.post(
        f"{service.api}/model-providers",
        json={"name": "Unused", "type": "openai", "config": config, "credential": {"api_key": "sk-unused"}},
    )
    config = {"model_name": "unused", "model_api": "openai.chat_completions"}
    model = await client.post(
        f"{service.api}/models",
        json={"name": "Unused", "provider_id": model_provider.json()["id"], "key": "unused", "config": config},
    )
    agent = await client.post(
        f"{service.api}/agents",
        json={
            "name": "Builder",
            "config": {"model": model.json()["key"], "default_environment_template_id": template.json()["id"]},
        },
    )
    assert agent.status_code == 201, agent.text
    service.provider, service.template, service.agent = provider.json(), template.json(), agent.json()
    return service


async def start(env: SimpleNamespace, text: str = "build it", **fields: object) -> dict:
    """A new thread whose first run is accepted; the receipt carries the thread and the run."""
    response = await new_thread(env, text, **fields)
    assert response.status_code == 201, response.text
    return response.json()


async def new_thread(env: SimpleNamespace, text: str, **fields: object) -> httpx2.Response:
    return await env.client.post(
        f"{env.api}/threads",
        json={"agent_id": env.agent["id"], "payload": {"content": [{"type": "text", "text": text}]}, **fields},
        headers={"idempotency-key": uuid4().hex},
    )


async def reserve(env: SimpleNamespace, template_id: str, **fields: object) -> dict:
    """A managed sandbox reserved from a template, in `creating` until maintenance creates it."""
    response = await env.client.post(f"{env.api}/environments", json={"template_id": template_id, **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def environment(env: SimpleNamespace, environment_id: str) -> dict:
    response = await env.client.get(f"{env.api}/environments/{environment_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def act(env: SimpleNamespace, method: str, path: str) -> tuple[int, dict]:
    """An environment command under the environment's current ETag."""
    current = await env.client.get(f"{env.api}/environments/{path.split('/')[0]}")
    response = await env.client.request(
        method, f"{env.api}/environments/{path}", headers={"if-match": current.headers["etag"]}
    )
    return response.status_code, response.json()


def reason(body: dict) -> str:
    return body["error"]["details"]["reason"]


async def interrupt(env: SimpleNamespace, run_id: str) -> None:
    response = await env.client.post(f"{env.api}/runs/{run_id}/interrupt")
    assert response.status_code == 200 and response.json()["status"] == "cancelled", response.text


async def backdate(env: SimpleNamespace, environment_id: str, **ago: timedelta) -> None:
    """Move an instance's clocks into the past, as waiting would."""
    values = {column: func.now() - delta for column, delta in ago.items()}
    async with transaction(env.runtime.storage) as session:
        await session.execute(update(EnvironmentRow).where(EnvironmentRow.id == environment_id).values(values))


async def follow_up(env: SimpleNamespace, thread_id: str, text: str) -> httpx2.Response:
    return await env.client.post(
        f"{env.api}/threads/{thread_id}/inbox",
        json={"agent_id": env.agent["id"], "payload": {"content": [{"type": "text", "text": text}]}},
        headers={"idempotency-key": uuid4().hex},
    )


async def unmount(env: SimpleNamespace, thread_id: str) -> None:
    """Remove the primary mount under the thread's current ETag."""
    thread = await env.client.get(f"{env.api}/threads/{thread_id}")
    removed = await env.client.delete(
        f"{env.api}/threads/{thread_id}/environments/workspace", headers={"if-match": thread.headers["etag"]}
    )
    assert removed.status_code == 204, removed.text


# External targets: the token test daemons accept, and requests against a registered target.
EXTERNAL_TOKEN = "external-target-token"


async def register(service: SimpleNamespace, endpoint: str, token: str = EXTERNAL_TOKEN) -> httpx2.Response:
    return await service.client.post(f"{service.api}/environments", json={"endpoint": endpoint, "token": token})


async def change(service: SimpleNamespace, environment_id: str, body: dict) -> httpx2.Response:
    path = f"{service.api}/environments/{environment_id}"
    current = await service.client.get(path)
    return await service.client.patch(path, json=body, headers={"if-match": current.headers["etag"]})


async def stored(service: SimpleNamespace, environment_id: str) -> EnvironmentRow:
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, environment_id)
    assert row is not None
    return row


async def target(service: SimpleNamespace, environment_id: str) -> Target:
    row = await stored(service, environment_id)
    return Target(row.id, external.account(row), {}, None)


def revealed(service: SimpleNamespace, row: EnvironmentRow) -> bytes:
    assert row.token is not None
    location = SecretLocation(row.organization_id, "environments", "token", row.id)
    return service.runtime.keys.reveal(Envelope.model_validate(row.token), location)


def details(response: httpx2.Response) -> dict:
    return response.json()["error"]["details"]
