"""An environment journey: a run's shell tool acts in its sandbox, which is then stopped, reused and deleted.

`local` runs on every host. `docker` needs a reachable Engine and the native execution image
(`make image-sandbox`). A hosted type needs its vendor account in the environment (`hosted_account`)
and creates billable sandboxes, which its journey deletes, after a failure too; hosted journeys run only when asked
for: `--hosted`, `-m hosted`, or `-k` naming their type. A journey whose dependency is missing is skipped, or fails
under `--require-all`.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

import docker
import httpx2
import pytest
from a13n_environment.management import Environment
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS
from docker.errors import DockerException, ImageNotFound

from .api import eventually, expect
from .scripted import tool_results
from .stack import docker_host, unavailable

pytestmark = pytest.mark.anyio

DOCKER_IMAGE = os.environ.get("SANDBOX_IMAGE", "a13n-sandbox:local")
HOSTED = ("e2b", "daytona", "modal", "vercel", "sprites", "runloop")


def engine() -> closing[docker.DockerClient]:
    return closing(docker.DockerClient(base_url=docker_host()))


def containers(environment_id: str) -> list:  # type: ignore[type-arg]
    with engine() as client:
        return client.containers.list(all=True, filters={"label": f"a13n.environment={environment_id}"})


@dataclass
class Backend:
    """A provider account, the recipe its template uses, and a direct view of the instances it holds."""

    type: str
    config: dict
    # A failed assertion prints the backend; its credential stays out.
    credential: dict | None = field(repr=False)
    recipe: dict
    directory: Path

    async def exists(self, environment_id: str) -> bool:
        if self.type == "local":
            return (self.directory / environment_id).is_dir()
        if self.type == "docker":
            return bool(containers(environment_id))
        adapter = await self._adapter(environment_id)
        try:
            return await adapter.reconcile() != "absent"
        finally:
            await adapter.close()

    async def remove(self, environment_id: str) -> None:
        """Delete what a failed journey left behind; a passing one already deleted its instance."""
        if self.type == "docker":
            for container in containers(environment_id):
                container.remove(force=True)
        elif self.type != "local":
            adapter = await self._adapter(environment_id)
            try:
                await adapter.destroy()
            finally:
                await adapter.close()

    async def _adapter(self, environment_id: str) -> Environment:
        # Acting as the owner, as the Service's lifecycle does, finds the target by the environment ID alone.
        [definition] = [item for item in BUILT_IN_ENVIRONMENT_PROVIDERS if item.type == self.type]
        return await definition.create(
            self.recipe,
            configuration=self.config,
            credential=self.credential,
            environment_id=environment_id,
            allow_create=True,
        )


def variables(config: pytest.Config, provider: str, *names: str) -> list[str]:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        unavailable(config, f"{provider} needs {', '.join(missing)}")
    return [os.environ[name] for name in names]


async def daytona_organization(api_key: str) -> str:
    async with httpx2.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://app.daytona.io/api/api-keys/current", headers={"authorization": f"Bearer {api_key}"}
        )
    return expect(response, 200)["organizationId"]


@asynccontextmanager
async def modal_app(token_id: str, token_secret: str) -> AsyncIterator[str]:
    """A fresh deployed Modal App to hold the journey's sandboxes, stopped afterwards."""
    import modal

    name = f"a13n-e2e-{uuid4().hex[:12]}"
    client = await modal.Client.from_credentials.aio(token_id, token_secret)
    await modal.App.lookup.aio(name, create_if_missing=True, client=client)
    try:
        yield name
    finally:
        cli = Path(sys.executable).with_name("modal")
        subprocess.run([str(cli), "app", "stop", "--yes", name], capture_output=True, check=True, timeout=60)


@asynccontextmanager
async def hosted_account(provider: str, config: pytest.Config) -> AsyncIterator[tuple[dict, dict, dict]]:
    """A hosted type's account configuration, credential and a small recipe, from the operator's variables.

    An organization or workspace name that only labels an account's backend defaults to `service-e2e`.
    """
    if provider == "e2b":
        [key] = variables(config, provider, "E2B_API_KEY")
        yield {}, {"api_key": key}, {"timeout_seconds": 900}
    elif provider == "daytona":
        [key] = variables(config, provider, "DAYTONA_API_KEY")
        organization = os.environ.get("DAYTONA_ORGANIZATION_ID") or await daytona_organization(key)
        yield {"organization_id": organization}, {"api_key": key}, {}
    elif provider == "modal":
        token_id, token_secret = variables(config, provider, "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
        async with modal_app(token_id, token_secret) as app:
            account = {"workspace": os.environ.get("MODAL_WORKSPACE", "service-e2e"), "app_name": app}
            yield account, {"token_id": token_id, "token_secret": token_secret}, {"timeout_seconds": 900}
    elif provider == "vercel":
        token, team, project = variables(config, provider, "VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")
        # A Hobby plan refuses sandboxes that may run longer than 45 minutes.
        yield {"team_id": team, "project_id": project}, {"api_key": token}, {"timeout_seconds": 900}
    elif provider == "sprites":
        token, organization = variables(config, provider, "SPRITES_TOKEN", "SPRITES_ORGANIZATION")
        yield {"organization": organization}, {"api_key": token}, {}
    else:
        [key] = variables(config, provider, "RUNLOOP_API_KEY")
        yield {"organization": os.environ.get("RUNLOOP_ORGANIZATION", "service-e2e")}, {"api_key": key}, {}


@asynccontextmanager
async def backend(provider: str, directory: Path, config: pytest.Config) -> AsyncIterator[Backend]:
    """The account of `provider` on this host."""
    if provider in HOSTED:
        async with hosted_account(provider, config) as (account, credential, recipe):
            yield Backend(provider, account, credential, recipe, directory)
        return
    if provider == "local":
        shell = [{"profile_id": "default", "executable": "/bin/sh"}]
        yield Backend(provider, {}, None, {"root": {"path": str(directory)}, "shell_profiles": shell}, directory)
        return
    try:
        with engine() as client:
            client.images.get(DOCKER_IMAGE)
    except ImageNotFound:
        unavailable(config, f"{DOCKER_IMAGE} is not built; run make image-sandbox")
    except DockerException as error:
        unavailable(config, f"No Docker Engine is reachable: {error}")
    # The account names no engine, so it uses the operator's.
    yield Backend(provider, {}, None, {"image": DOCKER_IMAGE}, directory)


# A hosted journey creates, stops, starts and deletes a sandbox, each of which may take minutes.
@pytest.mark.timeout(1500)
@pytest.mark.parametrize(
    "provider",
    ["local", "docker", *(pytest.param(provider, marks=pytest.mark.hosted(provider)) for provider in HOSTED)],
)
async def test_a_run_uses_its_environment_across_its_lifecycle(stack, provider, request) -> None:  # type: ignore[no-untyped-def]
    directory = stack.directory / "environments"
    directory.mkdir()
    async with backend(provider, directory, request.config) as account:
        cleanup: list[str] = []
        try:
            await use_environment(stack, account, cleanup)
        finally:
            for environment_id in cleanup:
                await account.remove(environment_id)


async def create_template(api, account: Backend) -> str:  # type: ignore[no-untyped-def]
    """A provider resource for the account and a template of its recipe."""
    body = {"type": account.type, "name": account.type.title(), "config": account.config}
    if account.credential is not None:
        body["credential"] = account.credential
    provider_row = await api.client.post("/api/v1/environment-providers", json=body)
    template = await api.client.post(
        "/api/v1/environment-templates",
        json={"name": "Box", "provider_id": expect(provider_row, 201)["id"], "config": {"recipe": account.recipe}},
    )
    return expect(template, 201)["id"]


async def delete(api, path: str, timeout: float) -> None:  # type: ignore[no-untyped-def]
    current = await api.client.get(path)
    deleting = await api.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert expect(deleting, 202)["status"] in {"deleting", "deleted"}

    async def deleted() -> bool:
        return expect(await api.client.get(path), 200)["status"] == "deleted"

    await eventually(deleted, timeout=timeout)


async def use_environment(stack, account: Backend, cleanup: list[str]) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    # Hosted sandboxes take longer to create, stop and delete than a local container.
    timeout = 120 if account.type in {"local", "docker"} else 300
    template_id = await create_template(api, account)
    types = expect(await api.client.get("/api/v1/provider-types/environment"), 200)["items"]
    [described] = [item for item in types if item["type"] == account.type]
    agent = await api.create_agent(
        "Builder", await api.create_model(model.base_url), default_environment_template_id=template_id
    )

    command = "echo live-$((6 * 7)) > proof.txt && cat proof.txt"
    if account.type == "docker":
        # Native execution must not inherit the sandbox image's root daemon identity.
        command = (
            'test "$(id -u)" = 1000 && test "$(id -g)" = 1000 '
            '&& test "$HOME" = /home/sandbox && test -w /tmp/a13n '
            '&& test "$(sudo -n id -u)" = 0 && ' + command
        )
    await model.call("shell_exec", {"command": command}, call_id="call_write", to="[write]")
    await model.say("Written.", to="[write]")
    receipt = await api.start(agent, "[write] Write the proof")
    thread_id = receipt["thread"]["id"]
    [mount] = receipt["run"]["environment_mounts"]
    environment_id, path = mount["environment_id"], f"/api/v1/environments/{mount['environment_id']}"
    cleanup.append(environment_id)
    run = await api.sealed(receipt["run"]["id"], timeout=timeout)
    assert (run["status"], run["output"]) == ("completed", "Written."), run["failure"]
    [_, answered] = await model.requests("[write]")
    assert "live-42" in tool_results(answered)[0]
    assert expect(await api.client.get(path), 200)["status"] == "ready"
    assert await account.exists(environment_id)

    # Stopping waits for no run; the next run starts the stopped instance, whose files survived.
    if described["supports_stop"]:
        current = await api.client.get(path)
        stopping = await api.client.post(f"{path}/stop", headers={"if-match": current.headers["etag"]})
        assert expect(stopping, 202)["status"] == "stopping"

        async def stopped() -> bool:
            return expect(await api.client.get(path), 200)["status"] == "stopped"

        await eventually(stopped, timeout=timeout)
    if account.type == "docker":
        # Exercise the native file helper after restart as well as shell execution.
        await model.call("view", {"file_path": "/workspace/proof.txt"}, call_id="call_read", to="[read]")
    else:
        await model.call("shell_exec", {"command": "cat proof.txt"}, call_id="call_read", to="[read]")
    await model.say("Read.", to="[read]")
    again = (await api.send(thread_id, agent, "[read] Read the proof"))["run"]
    assert again["environment_mounts"] == [mount]
    assert (await api.sealed(again["id"], timeout=timeout))["status"] == "completed"
    [_, answered] = await model.requests("[read]")
    assert "live-42" in tool_results(answered)[0]
    assert expect(await api.client.get(path), 200)["status"] == "ready"

    # Deletion requires the thread to stop mounting it first, then removes the instance.
    thread = await api.client.get(f"/api/v1/threads/{thread_id}")
    unmounted = await api.client.delete(
        f"/api/v1/threads/{thread_id}/environments/workspace", headers={"if-match": thread.headers["etag"]}
    )
    expect(unmounted, 204)
    await delete(api, path, timeout)
    assert not await account.exists(environment_id)


# E2B kills a sandbox at its timeout unless it is renewed; this is the shortest timeout the Service accepts.
E2B_TIMEOUT = 300


@pytest.mark.hosted("e2b")
@pytest.mark.timeout(1500)
async def test_a_ready_e2b_sandbox_is_renewed_past_its_timeout(stack, request) -> None:  # type: ignore[no-untyped-def]
    directory = stack.directory / "environments"
    directory.mkdir()
    async with backend("e2b", directory, request.config) as account:
        account = replace(account, recipe={"timeout_seconds": E2B_TIMEOUT})
        cleanup: list[str] = []
        try:
            await outlive_timeout(stack.api, account, cleanup)
        finally:
            for environment_id in cleanup:
                await account.remove(environment_id)


async def outlive_timeout(api, account: Backend, cleanup: list[str]) -> None:  # type: ignore[no-untyped-def]
    reserved = await api.client.post("/api/v1/environments", json={"template_id": await create_template(api, account)})
    environment_id = expect(reserved, 201)["id"]
    cleanup.append(environment_id)
    path = f"/api/v1/environments/{environment_id}"

    async def ready() -> bool:
        return expect(await api.client.get(path), 200)["status"] == "ready"

    await eventually(ready, timeout=300)
    # Past the timeout with no run connecting: only renewal keeps it alive.
    await asyncio.sleep(E2B_TIMEOUT + 60)
    view = expect(await api.client.get(path), 200)
    assert (view["status"], view["failure"]) == ("ready", None)
    assert await account.exists(environment_id)
    await delete(api, path, 300)
    assert not await account.exists(environment_id)
