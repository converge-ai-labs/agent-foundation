"""Provider resources of every kind: workspace ownership, write-only credentials, preconditions, tests and types."""

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

import anyio
import httpx2
import pytest
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration as HarnessDockerRecipe,
)
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import Base, short_session, transaction, violated_constraint
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers import service as providers
from a13n_service.resources.providers.schemas import ProviderCreate, ProviderUpdate
from a13n_service.resources.providers.tables import ModelProviderRow, WebProviderRow
from a13n_service.resources.web_providers.runtime import open_scrape_backend, open_search_backend
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.settings import Settings
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio

SECRET = "sk-test-secret-value"


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def add_workspace(service) -> str:  # type: ignore[no-untyped-def]
    workspace_id = new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(WorkspaceRow(id=workspace_id, organization_id=service.tenant.organization_id, name="Second"))
    return workspace_id


def principal(service, *grants: tuple[str | None, str], confined_to: str | None = None) -> Principal:  # type: ignore[no-untyped-def]
    """The bootstrapped principal with other grants; service functions trust the authenticated value they get.

    `confined_to` is how an API key of that workspace authenticates.
    """
    organization_id = service.tenant.organization_id
    return Principal(
        service.tenant.principal_id,
        "user",
        tuple(Grant(organization_id, workspace_id, BUILT_IN_ROLES[role]) for workspace_id, role in grants),
        WorkspaceScope(organization_id, confined_to) if confined_to else None,
    )


async def create(service, kind: str, body: dict, workspace_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """`workspace_id` names another workspace than the session's own."""
    headers = {"x-workspace-id": workspace_id} if workspace_id else {}
    response = await service.client.post(f"{service.api}/{kind}-providers", json=body, headers=headers)
    assert response.status_code == 201, response.text
    assert response.headers["etag"] == etag(response.json())
    return response.json()


async def test_a_provider_keeps_its_credential_write_only(service) -> None:  # type: ignore[no-untyped-def]
    body = {"type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}}
    created = await create(service, "model", body)
    assert created["workspace_id"] == service.tenant.workspace_id
    assert created["credential_configured"] is True
    for path in (f"/model-providers/{created['id']}", "/model-providers"):
        response = await service.client.get(service.api + path)
        assert response.status_code == 200, response.text
        assert SECRET not in response.text

    async with short_session(service.runtime.storage) as session:
        row = await session.get(ModelProviderRow, created["id"])
    assert row is not None and row.credential is not None and SECRET not in json.dumps(row.credential)
    envelope = Envelope.model_validate(row.credential)
    organization_id = service.tenant.organization_id
    location = SecretLocation(organization_id, "model_providers", "credential", row.id)
    assert json.loads(service.runtime.keys.reveal(envelope, location)) == {"api_key": SECRET}
    # The envelope is bound to its table, column and row; it cannot be replayed as another credential.
    with pytest.raises(ServiceError):
        service.runtime.keys.reveal(envelope, SecretLocation(organization_id, "web_providers", "credential", row.id))


async def test_patch_requires_the_current_etag_and_replaces_or_removes_the_credential(service) -> None:  # type: ignore[no-untyped-def]
    created = await create(service, "model", {"type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}})
    item = f"{service.api}/model-providers/{created['id']}"

    missing = await service.client.patch(item, json={"name": "Renamed"})
    assert missing.status_code == 428 and missing.json()["error"]["code"] == "precondition_required"
    stale = await service.client.patch(item, json={"name": "Renamed"}, headers={"if-match": '"x:1"'})
    assert stale.status_code == 412 and stale.json()["error"]["details"]["current_etag"] == etag(created)

    renamed = await service.client.patch(
        item, json={"name": "Renamed", "credential": {"api_key": "sk-rotated"}}, headers={"if-match": etag(created)}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed" and renamed.json()["version"] == created["version"] + 1
    current = renamed.headers["etag"]

    # Bearer authentication requires the credential, so removing it alone is refused ...
    refused = await service.client.patch(item, json={"credential": None}, headers={"if-match": current})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "credential"
    # ... and a configuration without authentication refuses a stored one until it is removed with it.
    kept = await service.client.patch(item, json={"config": {"auth_mode": "none"}}, headers={"if-match": current})
    assert kept.status_code == 400 and kept.json()["error"]["details"]["field"] == "credential"
    removed = await service.client.patch(
        item,
        json={"config": {"auth_mode": "none"}, "credential": None, "enabled": False},
        headers={"if-match": current},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["credential_configured"] is False
    assert removed.json()["config"] == {"auth_mode": "none"}
    assert removed.json()["enabled"] is False

    unchanged = await service.client.patch(item, json={"enabled": False}, headers={"if-match": etag(removed.json())})
    assert unchanged.json()["version"] == removed.json()["version"]

    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.target_id == created["id"])
                .order_by(AuditEventRow.occurred_at)
            )
        ).all()
    assert [(event.action, event.details) for event in events] == [
        ("model_provider.create", {}),
        ("model_provider.update", {"fields": ["credential", "name"]}),
        ("model_provider.update", {"fields": ["config", "credential", "enabled"]}),
    ]


async def test_a_change_compares_only_the_fields_it_sets(service) -> None:  # type: ignore[no-untyped-def]
    """A stored configuration stays as it was normalized when it was written, even if normalization changed since."""
    created = await create(service, "model", {"type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}})
    written = {"auth_mode": "bearer"}
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(ModelProviderRow).where(ModelProviderRow.id == created["id"]).values(config=written)
        )
    item = f"{service.api}/model-providers/{created['id']}"
    current = (await service.client.get(item)).json()
    renamed = await service.client.patch(item, json={"name": "Renamed"}, headers={"if-match": etag(current)})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["config"] == written and renamed.json()["credential_configured"]
    async with short_session(service.runtime.storage) as session:
        details = (
            await session.scalars(
                select(AuditEventRow.details).where(
                    AuditEventRow.target_id == created["id"], AuditEventRow.action == "model_provider.update"
                )
            )
        ).all()
    assert details == [{"fields": ["name"]}]


async def test_rows_of_an_archived_workspace_are_read_but_never_changed(service) -> None:  # type: ignore[no-untyped-def]
    workspace_id = service.tenant.workspace_id
    provider = await create(service, "model", {"type": "openai", "name": "Own", "credential": {"api_key": SECRET}})
    config = {"model_name": "gpt", "model_api": "openai.chat_completions"}
    model = await service.client.post(
        f"{service.api}/models", json={"provider_id": provider["id"], "key": "gpt", "name": "GPT", "config": config}
    )
    assert model.status_code == 201, model.text
    workspace = (await service.client.get(service.workspace)).json()
    archived = await service.client.post(f"{service.workspace}/archive", headers={"if-match": etag(workspace)})
    assert archived.status_code == 200, archived.text

    for path, row, current in (
        (f"{service.api}/model-providers/{provider['id']}", provider, etag(provider)),
        (f"{service.api}/models/gpt", model.json(), model.headers["etag"]),
    ):
        refused = await service.client.patch(path, json={"name": "Renamed"}, headers={"if-match": current})
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["details"] == {"kind": "workspace", "id": workspace_id}
        assert (await service.client.get(path)).json()["name"] == row["name"]


async def test_config_and_credential_follow_the_type_schema(service) -> None:  # type: ignore[no-untyped-def]
    async def refused(kind: str, body: dict) -> dict:
        response = await service.client.post(f"{service.api}/{kind}-providers", json=body)
        assert response.status_code in {400, 503}, response.text
        assert SECRET not in response.text
        return response.json()["error"]

    named = {"name": "Account"}
    assert (await refused("web", {**named, "type": "tavily"}))["details"]["field"] == "credential"
    forbidden = await refused("web", {**named, "type": "duckduckgo", "credential": {"api_key": SECRET}})
    assert forbidden["details"]["field"] == "credential"
    extra = await refused("web", {**named, "type": "tavily", "credential": {"api_key": SECRET, "extra": SECRET}})
    assert extra["details"]["field"] == "credential"
    config = await refused("model", {**named, "type": "openai", "config": {"unknown": 1}, "credential": {}})
    assert config["details"]["field"] == "config"
    unknown = await refused("web", {**named, "type": "nope"})
    assert unknown["code"] == "unavailable" and unknown["details"]["dependency"] == "web:nope"
    # The development-only local provider is not offered unless the operator allows it.
    environment = await refused("environment", {**named, "type": "local"})
    assert environment["details"]["dependency"] == "environment:local"
    # The workspace comes from the request, never from the body.
    placed = await refused("web", {**named, "type": "duckduckgo", "workspace_id": service.tenant.workspace_id})
    assert placed["details"]["fields"][0] == {"field": "body.workspace_id", "reason": "extra_forbidden"}

    created = await create(service, "web", {**named, "type": "duckduckgo"})
    assert created["config"] == {} and created["credential_configured"] is False


async def test_lists_return_the_rows_of_the_request_workspace(service) -> None:  # type: ignore[no-untyped-def]
    first, second = service.tenant.workspace_id, await add_workspace(service)
    rows = {
        name: (await create(service, "web", {"type": "duckduckgo", "name": name}, workspace_id=workspace))["id"]
        for name, workspace in (("one", first), ("two", first), ("second", second))
    }

    collection = f"{service.api}/web-providers"
    own = await service.client.get(collection)
    assert {item["id"] for item in own.json()["items"]} == {rows["one"], rows["two"]}
    other = await service.client.get(collection, headers={"x-workspace-id": second})
    assert [item["id"] for item in other.json()["items"]] == [rows["second"]]
    page = await service.client.get(collection, params={"limit": 1})
    rest = await service.client.get(collection, params={"limit": 1, "cursor": page.json()["next_cursor"]})
    assert len(page.json()["items"]) == 1 and len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
    # A cursor continues only the workspace's list that issued it.
    crossed = await service.client.get(
        collection, params={"cursor": page.json()["next_cursor"]}, headers={"x-workspace-id": second}
    )
    assert crossed.status_code == 400 and crossed.json()["error"]["code"] == "invalid_cursor"

    storage = service.runtime.storage
    api_key = principal(service, (None, "admin"), confined_to=first)
    visible = await providers.list_providers(storage, api_key, WebProviderRow, first, limit=50, cursor=None)
    assert {item.id for item in visible.items} == {rows["one"], rows["two"]}
    with pytest.raises(ServiceError, match="cannot perform"):
        await providers.list_providers(storage, api_key, WebProviderRow, second, limit=50, cursor=None)
    # A row of another workspace is not found rather than forbidden, revealing nothing.
    with pytest.raises(ServiceError) as hidden:
        await providers.get_provider(storage, api_key, WebProviderRow, first, rows["second"])
    assert hidden.value.code == "not_found"
    outsider = Principal(service.tenant.principal_id, "user", (Grant("org_elsewhere", None, BUILT_IN_ROLES["admin"]),))
    with pytest.raises(ServiceError) as unknown:
        await providers.list_providers(storage, outsider, WebProviderRow, first, limit=50, cursor=None)
    assert unknown.value.code == "not_found"


async def test_only_workspace_writers_change_its_providers(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    storage, context = runtime.storage, {"registry": runtime.registry, "keys": runtime.keys}
    workspace_id, second = service.tenant.workspace_id, await add_workspace(service)
    body = ProviderCreate(type="duckduckgo", name="Search")

    builder = principal(service, (workspace_id, "builder"))
    api_key = principal(service, (None, "admin"), confined_to=workspace_id)
    for actor in (builder, api_key):
        created = await providers.create_provider(storage, actor, WebProviderRow, workspace_id, body, **context)
        assert created.workspace_id == workspace_id
        updated = await providers.update_provider(
            storage,
            actor,
            WebProviderRow,
            workspace_id,
            created.id,
            ProviderUpdate(enabled=False),
            if_match=f'"{created.id}:{created.version}"',
            **context,
        )
        assert not updated.enabled
        # Neither reaches another workspace of the organization.
        with pytest.raises(ServiceError, match="cannot perform"):
            await providers.create_provider(storage, actor, WebProviderRow, second, body, **context)

    viewer = principal(service, (workspace_id, "viewer"))
    with pytest.raises(ServiceError, match="cannot perform"):
        await providers.create_provider(storage, viewer, WebProviderRow, workspace_id, body, **context)
    readable = await providers.get_provider(storage, viewer, WebProviderRow, workspace_id, created.id)
    with pytest.raises(ServiceError, match="cannot perform"):
        await providers.test_provider(
            storage,
            viewer,
            WebProviderRow,
            workspace_id,
            readable.id,
            policy=runtime.endpoint_policy,
            settings=runtime.settings.providers,
            **context,
        )


class Replying(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "text/plain" if body == b"OK" else "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@contextmanager
def serving(handler: type[Replying]) -> Iterator[int]:
    """`handler` on a loopback port, for the length of the block."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def models_endpoint() -> Iterator[tuple[str, list[str], list[dict[str, str]]]]:
    """A local OpenAI-compatible `GET /v1/models` that accepts only the bearer token `good`; it records each
    request's path and headers."""
    requests: list[str] = []
    headers: list[dict[str, str]] = []

    class Handler(Replying):
        def do_GET(self) -> None:
            requests.append(self.path)
            headers.append({name.lower(): value for name, value in self.headers.items()})
            self.reply(200 if self.headers.get("authorization") == "Bearer good" else 401, b'{"data": []}')

    with serving(Handler) as port:
        yield f"http://127.0.0.1:{port}/v1", requests, headers


async def test_provider_test_probes_without_paid_calls(service) -> None:  # type: ignore[no-untyped-def]
    def openai(name: str, base_url: str) -> dict:
        return {
            "type": "openai",
            "name": name,
            "config": {"base_url": base_url},
            "credential": {"api_key": "good"},
        }

    with models_endpoint() as (base_url, requests, _):
        created = await create(service, "model", openai("Local", base_url))
        item = f"{service.api}/model-providers/{created['id']}"
        passed = await service.client.post(item + "/test")
        assert passed.status_code == 200, passed.text
        assert passed.json() == {
            "provider_id": created["id"],
            "provider_version": created["version"],
            "status": "succeeded",
            "message": None,
        }
        rotated = await service.client.patch(
            item, json={"credential": {"api_key": "bad"}}, headers={"if-match": etag(created)}
        )
        failed = (await service.client.post(item + "/test")).json()
        assert failed["status"] == "failed" and failed["provider_version"] == rotated.json()["version"]
        assert failed["message"] and "bad" not in failed["message"]
        assert requests == ["/v1/models", "/v1/models"]

        tavily = {"type": "tavily", "name": "Tavily", "credential": {"api_key": SECRET}}
        web = await create(service, "web", tavily)
        unsupported = (await service.client.post(f"{service.api}/web-providers/{web['id']}/test")).json()
        assert unsupported["status"] == "unsupported"
        assert len(requests) == 2

    # Outside the operator's endpoint allowlist, the probe is refused before any connection.
    private = await create(service, "model", openai("Private", "http://10.1.2.3/v1"))
    denied = (await service.client.post(f"{service.api}/model-providers/{private['id']}/test")).json()
    assert denied["status"] == "failed"


async def test_a_configuration_change_never_carries_the_stored_credential(service) -> None:  # type: ignore[no-untyped-def]
    """A credential is bound to the endpoint it was entered for; moving the endpoint must replace or remove it."""

    def account(base_url: str) -> dict:
        return {"config": {"base_url": base_url}, "credential": {"api_key": "good"}}

    with models_endpoint() as (first, first_requests, _), models_endpoint() as (second, second_requests, _):
        created = await create(service, "model", {"type": "openai", "name": "OpenAI", **account(first)})
        item = f"{service.api}/model-providers/{created['id']}"

        moved = await service.client.patch(
            item, json={"config": {"base_url": second}}, headers={"if-match": etag(created)}
        )
        assert moved.status_code == 400 and moved.json()["error"]["details"]["field"] == "credential"
        assert (await service.client.post(item + "/test")).json()["status"] == "succeeded"
        assert first_requests == ["/v1/models"] and second_requests == []

        rebound = await service.client.patch(item, json=account(second), headers={"if-match": etag(created)})
        assert rebound.status_code == 200, rebound.text
        assert (await service.client.post(item + "/test")).json()["status"] == "succeeded"
        assert first_requests == ["/v1/models"] and second_requests == ["/v1/models"]


async def test_model_provider_headers_are_write_only_secrets_sent_with_each_request(service) -> None:  # type: ignore[no-untyped-def]
    """Extra header values are encrypted per name, never returned, and bound to the configuration like the
    credential."""
    gateway = "gw-secret-value"
    with models_endpoint() as (base_url, requests, received):
        created = await create(
            service,
            "model",
            {
                "type": "openai",
                "name": "Gateway",
                "config": {"base_url": base_url},
                "credential": {"api_key": "good"},
                "extra_headers": {"X-Gateway-Key": gateway, "x-team": "research"},
            },
        )
        assert created["header_names"] == ["x-gateway-key", "x-team"]
        item = f"{service.api}/model-providers/{created['id']}"
        for path in (item, f"{service.api}/model-providers"):
            assert gateway not in (await service.client.get(path)).text
        assert (await service.client.post(item + "/test")).json()["status"] == "succeeded"
        assert (received[-1]["x-gateway-key"], received[-1]["x-team"]) == (gateway, "research")

        # Each value is encrypted for its row and its name: one cannot be revealed as another.
        async with short_session(service.runtime.storage) as session:
            row = await session.get(ModelProviderRow, created["id"])
        assert row is not None and gateway not in json.dumps(row.extra_headers)
        envelope = Envelope.model_validate(row.extra_headers["x-gateway-key"])

        def location(name: str) -> SecretLocation:
            return SecretLocation(service.tenant.organization_id, "model_providers", f"extra_headers.{name}", row.id)

        assert service.runtime.keys.reveal(envelope, location("x-gateway-key")) == gateway.encode()
        with pytest.raises(ServiceError):
            service.runtime.keys.reveal(envelope, location("x-team"))

        # `null` removes a header, names left out keep their values, and a new credential keeps them all.
        updated = await service.client.patch(
            item,
            json={"extra_headers": {"x-team": None}, "credential": {"api_key": "good"}},
            headers={"if-match": etag(created)},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["header_names"] == ["x-gateway-key"]
        assert (await service.client.post(item + "/test")).json()["status"] == "succeeded"
        assert received[-1]["x-gateway-key"] == gateway and "x-team" not in received[-1]
        assert requests == ["/v1/models", "/v1/models"]

        # Authentication and transport headers belong to the provider and the HTTP client.
        current = {"if-match": etag(updated.json())}
        reserved = await service.client.patch(
            item, json={"extra_headers": {"Authorization": "Bearer other"}}, headers=current
        )
        assert reserved.status_code == 400 and reserved.json()["error"]["details"]["field"] == "extra_headers"
        transport = await service.client.patch(item, json={"extra_headers": {"host": "elsewhere"}}, headers=current)
        assert transport.status_code == 400 and "elsewhere" not in transport.text

        # A new endpoint inherits neither the credential nor a header value.
        moved = {"config": {"base_url": "http://127.0.0.1:9/v1"}, "credential": {"api_key": "good"}}
        kept = await service.client.patch(item, json=moved, headers=current)
        assert kept.status_code == 400 and kept.json()["error"]["details"]["field"] == "extra_headers"
        rebound = await service.client.patch(
            item, json={**moved, "extra_headers": {"x-gateway-key": None}}, headers=current
        )
        assert rebound.status_code == 200 and rebound.json()["header_names"] == []

    # Only model providers send extra headers.
    web = await service.client.post(
        f"{service.api}/web-providers",
        json={"type": "duckduckgo", "name": "Search", "extra_headers": {"x-team": "a"}},
    )
    assert web.status_code == 400 and web.json()["error"]["details"]["field"] == "extra_headers"

    async with short_session(service.runtime.storage) as session:
        details = (
            await session.scalars(select(AuditEventRow.details).where(AuditEventRow.target_id == created["id"]))
        ).all()
    assert {"fields": ["credential", "extra_headers"]} in details and gateway not in json.dumps(details)


@contextmanager
def docker_engine() -> Iterator[tuple[str, list[str]]]:
    """A Docker Engine API that answers only a ping; it records every request."""
    requests: list[str] = []

    class Handler(Replying):
        def do_GET(self) -> None:
            requests.append(f"GET {self.path}")
            self.reply(200, b"OK") if self.path == "/_ping" else self.reply(404, b"{}")

        def do_POST(self) -> None:
            requests.append(f"POST {self.path}")
            self.reply(404, b"{}")

    with serving(Handler) as port:
        yield f"tcp://127.0.0.1:{port}", requests


def closed_port() -> int:
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        return unused.getsockname()[1]


async def test_environment_provider_tests_only_read_the_engine(  # type: ignore[no-untyped-def]
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def tested(body: dict) -> dict:
        created = await create(service, "environment", {"name": body["type"], **body})
        response = await service.client.post(f"{service.api}/environment-providers/{created['id']}/test")
        assert response.status_code == 200, response.text
        return response.json()

    with docker_engine() as (docker_host, requests):
        assert (await tested({"type": "docker", "config": {"docker_host": docker_host}}))["status"] == "succeeded"
    # A ping alone: nothing is pulled, created or started.
    assert requests == ["GET /_ping"]
    unreachable = await tested({"type": "docker", "config": {"docker_host": f"tcp://127.0.0.1:{closed_port()}"}})
    assert (unreachable["status"], unreachable["message"]) == ("failed", "provider_unavailable")
    # A name that does not resolve now is unavailable, not denied: a resolution failure is transient.
    resolve = anyio.getaddrinfo

    async def unresolved(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == "engine.test":
            raise socket.gaierror(socket.EAI_NONAME, "unknown name")
        return await resolve(host, *args, **kwargs)

    monkeypatch.setattr(anyio, "getaddrinfo", unresolved)
    transient = await tested({"type": "docker", "config": {"docker_host": "tcp://engine.test:2375"}})
    assert (transient["status"], transient["message"]) == ("failed", "provider_unavailable")


async def test_environment_provider_policy_refuses_http_before_dialing(  # type: ignore[no-untyped-def]
    serve, settings: Settings
) -> None:
    # Without an exact HTTP exception, the strict operator policy refuses TCP before dialing.
    providers = settings.providers.model_copy(update={"require_https": True, "http_origins": ()})
    with docker_engine() as (docker_host, requests):
        async with serve(settings=settings.model_copy(update={"providers": providers})) as strict:
            created = await create(
                strict, "environment", {"type": "docker", "name": "Docker", "config": {"docker_host": docker_host}}
            )
            denied = (await strict.client.post(f"{strict.api}/environment-providers/{created['id']}/test")).json()
    assert (denied["status"], denied["message"]) == ("failed", "provider_endpoint_denied")
    assert requests == []


async def test_docker_accounts_name_only_remote_engines_the_endpoint_policy_allows(  # type: ignore[no-untyped-def]
    serve, settings: Settings
) -> None:
    """An engine runs containers as root on its host: an account that names none uses the operator's, and one that
    names an engine reaches it only over TCP or HTTPS, checked by the endpoint policy before any dial."""
    providers = settings.providers.model_copy(update={"require_https": True, "http_origins": ()})
    async with serve(settings=settings.model_copy(update={"providers": providers})) as service:
        collection = f"{service.api}/environment-providers"
        docker = {"type": "docker", "name": "Docker"}
        for docker_host in (
            "unix:///var/run/docker.sock",
            "ssh://root@127.0.0.1",
            "npipe:////./pipe/docker_engine",
            "tcp://127.0.0.1",
            "tcp://root:hunter2@127.0.0.1:2375",
            "tcp://127.0.0.1:2375/v1.45",
        ):
            refused = await service.client.post(collection, json={**docker, "config": {"docker_host": docker_host}})
            assert refused.status_code == 400, (docker_host, refused.text)
            assert refused.json()["error"]["details"]["field"] == "config" and "hunter2" not in refused.text
        assert (await create(service, "environment", docker))["config"] == {}

        with docker_engine() as (docker_host, requests):
            remote = await create(service, "environment", {**docker, "config": {"docker_host": docker_host}})
            tested = (await service.client.post(f"{collection}/{remote['id']}/test")).json()
        assert (tested["status"], tested["message"]) == ("failed", "provider_endpoint_denied")
        assert requests == []


async def _docker_template(service: SimpleNamespace, name: str, recipe: dict) -> httpx2.Response:
    provider = await create(service, "environment", {"type": "docker", "name": name})
    body = {"name": name, "provider_id": provider["id"], "config": {"recipe": recipe}}
    return await service.client.post(f"{service.api}/environment-templates", json=body)


async def _mount_template(service: SimpleNamespace, name: str, source: str) -> int:
    recipe = {"mounts": [{"source": source, "target": "/data"}]}
    return (await _docker_template(service, name, recipe)).status_code


async def test_docker_recipes_bind_no_host_directory_by_default(service) -> None:  # type: ignore[no-untyped-def]
    refused = await _docker_template(service, "default", {"mounts": [{"source": "/srv/shared/data", "target": "/d"}]})
    assert refused.status_code == 400
    # The recipe type's own message says why; the value is not echoed.
    reason = refused.json()["error"]["details"]["reason"]
    assert "Host mount sources must lie below a directory the operator allows" in reason
    assert "/srv/shared/data" not in refused.text


async def test_docker_recipes_keep_worker_buffers_within_the_harness_defaults(service) -> None:  # type: ignore[no-untyped-def]
    """What the shared worker buffers or runs for a container stays within the Harness defaults."""
    for index, name in enumerate(
        (
            "max_file_bytes",
            "max_query_entries",
            "max_output_preview_bytes",
            "max_output_bytes_per_stream",
            "max_spool_bytes",
            "max_concurrent_processes",
        )
    ):
        ceiling = HarnessDockerRecipe.model_fields[name].default
        refused = await _docker_template(service, f"over-{index}", {name: ceiling + 1})
        assert refused.status_code == 400, name
        assert refused.json()["error"]["details"]["reason"].startswith(f"invalid for docker: {name}: ")
    assert (await _docker_template(service, "within", {"max_file_bytes": 1 << 20})).status_code == 201


async def test_docker_recipes_bind_only_host_directories_the_operator_allows(serve, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    roots = settings.environments.model_copy(update={"docker_mount_roots": (PurePosixPath("/srv/shared"),)})
    async with serve(settings=settings.model_copy(update={"environments": roots})) as service:
        assert await _mount_template(service, "inside", "/srv/shared/data") == 201
        for index, source in enumerate(("/srv/shared-other", "/srv", "/var/run/docker.sock", "/srv/shared/../../etc")):
            assert await _mount_template(service, f"outside-{index}", source) == 400, source


async def test_templates_use_only_providers_of_their_workspace(service) -> None:  # type: ignore[no-untyped-def]
    """A template's provider runs the environments created from it, so it must be one of the template's workspace."""
    own = await create(service, "environment", {"type": "docker", "name": "Own"})
    other = await create(service, "environment", {"type": "docker", "name": "Other"}, await add_workspace(service))
    templates = f"{service.api}/environment-templates"
    hidden = await service.client.post(templates, json={"name": "Box", "provider_id": other["id"]})
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["details"] == {"kind": "environment_provider", "id": other["id"]}
    created = await service.client.post(templates, json={"name": "Box", "provider_id": own["id"]})
    assert created.status_code == 201, created.text
    moved = await service.client.patch(
        f"{templates}/{created.json()['id']}",
        json={"provider_id": other["id"]},
        headers={"if-match": etag(created.json())},
    )
    assert moved.status_code == 404, moved.text


async def test_provider_types_describe_each_registered_definition(service) -> None:  # type: ignore[no-untyped-def]
    async def types(kind: str) -> dict[str, dict]:
        response = await service.client.get(f"/api/v1/provider-types/{kind}")
        assert response.status_code == 200, response.text
        return {item["type"]: item for item in response.json()["items"]}

    models = await types("model")
    openai = models["openai"]
    assert openai["display_name"] == "OpenAI" and openai["supports_test"] is True
    assert openai["model_apis"] == ["openai.responses", "openai.chat_completions"]
    assert openai["default_model_api"] == "openai.responses"
    assert openai["model_api_labels"] == {
        "openai.responses": "OpenAI Responses",
        "openai.chat_completions": "OpenAI Chat Completions",
    }
    assert openai["credential_schema"]["properties"]["api_key"]
    assert openai["authentication"]["mode"] == "required"
    assert models["vercel"]["supports_test"] is False
    assert models["mistral"]["model_apis"] == ["mistral.chat_completions"]
    assert "mistral_prompt_cache_key" in models["mistral"]["settings_schemas"]["mistral.chat_completions"]["properties"]
    # Every calling API of every type describes its native settings, including the unified thinking levels.
    for described in models.values():
        assert set(described["settings_schemas"]) == set(described["model_apis"]) == set(described["model_api_labels"])
    responses = openai["settings_schemas"]["openai.responses"]
    assert responses["additionalProperties"] is False
    thinking = [value for option in responses["properties"]["thinking"]["anyOf"] for value in option.get("enum", [])]
    assert {"low", "high"} <= set(thinking)
    assert "default" not in responses["properties"]["max_tokens"]
    assert openai["supports_stop"] is None and openai["operations"] is None

    web = await types("web")
    assert web["tavily"]["operations"] == ["search", "scrape"] and web["brave"]["operations"] == ["search"]
    assert web["duckduckgo"]["credential_schema"] is None and web["tavily"]["supports_test"] is False
    assert web["tavily"]["settings_schemas"] is None

    assert {name: item["supports_test"] for name, item in (await types("connector")).items()} == {"composio": True}
    lifecycle = ("supports_stop", "supports_destroy", "supports_test")
    environments = await types("environment")
    # External envd targets are no provider type; Sprites cannot stop, and only a Docker account can be tested.
    assert {name: tuple(item[flag] for flag in lifecycle) for name, item in environments.items()} == {
        "daytona": (True, True, False),
        "docker": (True, True, True),
        "e2b": (True, True, False),
        "modal": (True, True, False),
        "runloop": (True, True, False),
        "sprites": (False, True, False),
        "vercel": (True, True, False),
    }
    # The Docker forms state the operator's policy and the worker's limits.
    docker = environments["docker"]
    assert "tcp://host:port" in docker["configuration_schema"]["properties"]["docker_host"]["description"]
    recipe = docker["environment_schema"]["properties"]
    assert "a directory the operator allows" in recipe["mounts"]["description"]
    assert recipe["max_file_bytes"]["maximum"] == HarnessDockerRecipe.model_fields["max_file_bytes"].default
    assert (await service.client.get("/api/v1/provider-types/trace")).status_code == 400


async def test_model_settings_follow_the_schema_of_their_calling_api(service) -> None:  # type: ignore[no-untyped-def]
    """The schema the provider types expose is the one the Service checks settings against."""
    check = service.runtime.registry.check_model_settings
    check("openai.responses", {"thinking": "high", "max_tokens": 1024}, field="settings")
    check("bedrock.converse", {"bedrock_guardrail_config": {"guardrailIdentifier": "g"}}, field="settings")
    check(
        "mistral.chat_completions",
        {"mistral_prompt_cache_key": "cache", "extra_headers": {"x-team": "team"}},
        field="settings",
    )
    with pytest.raises(ServiceError) as unknown:
        check("anthropic.messages", {"thinkng": True}, field="model_settings")
    assert unknown.value.details["field"] == "model_settings"
    with pytest.raises(ServiceError) as mistyped:
        check("openai.chat_completions", {"max_tokens": "many"}, field="model_settings")
    assert mistyped.value.details["field"] == "model_settings.max_tokens"
    with pytest.raises(ServiceError) as level:
        check("openai.responses", {"thinking": "maximal"}, field="settings")
    assert level.value.details["field"] == "settings.thinking"
    # The provider resource owns the transport and the operator its timeouts; the model's name alone selects the
    # upstream model, and server-side state and tools of the provider account stay out of reach.
    for model_api, key, value in (
        ("openai.responses", "extra_body", {"model": "other"}),
        ("openai.chat_completions", "extra_headers", {"authorization": "Bearer other"}),
        ("mistral.chat_completions", "extra_headers", {"authorization": "Bearer other"}),
        ("mistral.chat_completions", "openai_store", True),
        ("anthropic.messages", "timeout", 30),
        ("openai.responses", "openai_previous_response_id", "resp_other"),
        ("openai.responses", "openai_conversation_id", "conv_other"),
        ("openai.responses", "openai_native_tools", [{"type": "file_search", "vector_store_ids": ["vs_other"]}]),
        ("bedrock_mantle.responses", "openai_native_tools", [{"type": "web_search"}]),
        ("openrouter.chat_completions", "openrouter_models", ["other"]),
        ("bedrock.converse", "bedrock_inference_profile", "other"),
        ("bedrock.converse", "bedrock_additional_model_requests_fields", {"modelId": "other"}),
        ("google.generate_content", "google_cached_content", "cachedContents/other"),
    ):
        with pytest.raises(ServiceError) as refused:
            check(model_api, {key: value}, field="model_settings")
        expected = f"model_settings.{key}" if key in {"extra_body", "extra_headers"} else "model_settings"
        assert refused.value.details["field"] == expected, key


async def test_web_backends_carry_each_selected_account(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    organization_id, workspace_id = service.tenant.organization_id, service.tenant.workspace_id
    scope = WorkspaceScope(organization_id, workspace_id)
    admin = principal(service, (None, "admin"))
    accounts = {}
    for key in ("account-a", "account-b"):
        body = {"type": "tavily", "name": key, "credential": {"api_key": key}}
        accounts[key] = (await create(service, "web", body))["id"]
    context = {"registry": runtime.registry, "keys": runtime.keys, "policy": runtime.endpoint_policy}

    async with short_session(runtime.storage) as session:
        selected = {
            key: await providers.resolve_provider(session, admin, WebProviderRow, scope, provider_id)
            for key, provider_id in accounts.items()
        }
    for key, provider in selected.items():
        async with open_search_backend(provider, SearchOptions(max_results=3), **context) as search:
            assert search.backend_id == accounts[key]
            assert search.provider.credential.api_key.get_secret_value() == key  # type: ignore[attr-defined]
        async with open_scrape_backend(provider, ScrapeOptions(), **context) as scrape:
            assert scrape.backend_id == accounts[key]

    item = f"{service.api}/web-providers/{accounts['account-b']}"
    current = (await service.client.get(item)).headers["etag"]
    assert (await service.client.patch(item, json={"enabled": False}, headers={"if-match": current})).status_code == 200
    elsewhere = WorkspaceScope(organization_id, await add_workspace(service))
    async with short_session(runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await providers.resolve_provider(session, admin, WebProviderRow, scope, accounts["account-b"])
        assert refused.value.code == "disabled"
        with pytest.raises(ServiceError) as hidden:
            await providers.resolve_provider(session, admin, WebProviderRow, elsewhere, accounts["account-a"])
        assert hidden.value.code == "not_found"


async def test_the_database_keeps_provider_references_within_their_workspace(service) -> None:  # type: ignore[no-untyped-def]
    """A reference written past the service's checks still cannot name a provider of another workspace."""
    organization_id, first = service.tenant.organization_id, service.tenant.workspace_id
    second = await add_workspace(service)

    async def accounts(kind: str, type_: str, **body: object) -> list[str]:
        """A provider of this workspace the rows below use, then one of the second workspace."""
        return [
            (await create(service, kind, {"type": type_, "name": type_, **body}, workspace_id=workspace))["id"]
            for workspace in (first, second)
        ]

    own_model, other_model = await accounts("model", "openai", credential={"api_key": SECRET})
    own_environment, other_environment = await accounts("environment", "docker")
    _, other_connector = await accounts("connector", "composio", credential={"api_key": SECRET})

    config = {"model_name": "gpt", "model_api": "openai.chat_completions"}
    model = {"provider_id": own_model, "key": "gpt", "name": "GPT", "config": config}
    template = {"name": "Box", "provider_id": own_environment}
    connection = {"type": "mcp", "name": "Remote", "config": {"url": "http://127.0.0.1:9/mcp"}, "auth": "oauth"}
    created = {}
    for name, path, body in (
        ("model", f"{service.api}/models", model),
        ("template", f"{service.api}/environment-templates", template),
        ("connection", f"{service.api}/connections", connection),
    ):
        response = await service.client.post(path, json=body)
        assert response.status_code == 201, response.text
        created[name] = response.json().get("id")
    created["environment"] = new_object_id("env")
    async with transaction(service.runtime.storage) as session:
        created["model"] = await session.scalar(select(ModelRow.id).where(ModelRow.key == "gpt"))
        session.add(
            EnvironmentRow(
                id=created["environment"],
                organization_id=organization_id,
                workspace_id=first,
                provider_id=own_environment,
                template_id=created["template"],
                name="box",
                status="deleted",
                generation=0,
                created_by_id=service.tenant.principal_id,
            )
        )

    account = {"type": "composio", "auth": "account"}
    cases: list[tuple[type[Base], str, str, dict[str, object]]] = [
        (ModelRow, created["model"], "model_providers", {"provider_id": other_model}),
        (EnvironmentTemplateRow, created["template"], "environment_providers", {"provider_id": other_environment}),
        (EnvironmentRow, created["environment"], "environment_providers", {"provider_id": other_environment}),
        (
            ConnectionRow,
            created["connection"],
            "connector_providers",
            {**account, "connector_provider_id": other_connector},
        ),
    ]
    for table, row_id, providers_table, values in cases:
        with pytest.raises(IntegrityError) as refused:
            async with transaction(service.runtime.storage) as session:
                await session.execute(update(table).where(table.__table__.c.id == row_id).values(values))
        assert violated_constraint(refused.value) == f"fk_{table.__tablename__}_workspace_id_{providers_table}"
