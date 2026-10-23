"""Real HTTP queries and PostgreSQL authority checks, independent of monitoring collection."""

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.metrics import Destination, InstantQuery, Queries, RangeQuery
from a13n_service.resources.metrics_data_sources import service as sources
from a13n_service.runs.environments.monitoring.query import EnvironmentQuery, expression, query_environment
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import GrantRow, PrincipalRow
from pydantic import ValidationError

pytestmark = pytest.mark.anyio
RESULT = {"status": "success", "data": {"resultType": "matrix", "result": []}}


@asynccontextmanager
async def backend(*, status=200, payload=None, wait=None):
    calls = []
    tasks = []

    async def respond(reader, writer):
        try:
            async with asyncio.timeout(5):
                raw = (await reader.readuntil(b"\r\n\r\n")).decode()
                lines = raw.split("\r\n")
                headers = dict(line.lower().split(": ", 1) for line in lines[1:] if ": " in line)
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                calls.append((lines[0], headers, parse_qs(body.decode())))
                if wait is not None:
                    await wait()
                content = json.dumps(RESULT if payload is None else payload).encode()
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {len(content)}\r\n"
                    "Location: http://127.0.0.1:9/credential-leak\r\nConnection: close\r\n\r\n".encode()
                    + content
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        tasks.append(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    async with server:
        try:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", calls
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def create_source(service, endpoint):
    response = await service.client.post(
        f"{service.api}/metrics-data-sources",
        json={
            "name": "Query only",
            "config": {
                "collection": "external",
                "query": {"endpoint": endpoint + "/gateway", "scope_labels": {"tenant": "ours"}},
            },
            "query_credentials": {"headers": {"Authorization": "Bearer query-fixture"}},
        },
    )
    assert response.status_code == 201, response.text
    return response


async def builder(service):
    tenant = service.tenant
    principal_id = new_object_id("usr")
    async with transaction(service.runtime.storage) as session:
        session.add(PrincipalRow(id=principal_id, kind="user", name="builder", email=f"{principal_id}@example.com"))
        await session.flush()
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                principal_id=principal_id,
                role="builder",
                created_by_id=tenant.principal_id,
            )
        )
    return Principal(
        principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )


async def environment(service, source_id, owner_id):
    tenant = service.tenant
    environment_id = new_object_id("env")
    async with transaction(service.runtime.storage) as session:
        session.add(
            EnvironmentRow(
                id=environment_id,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                name="Private",
                status="ready",
                device_id='device"\\\n',
                endpoint="http://127.0.0.1:9",
                token={"test": "placeholder"},
                owner_principal_id=owner_id,
                created_by_id=tenant.principal_id,
                metrics_data_source_id=source_id,
            )
        )
    return environment_id


async def test_query_api_uses_separate_credentials(service):
    async with backend() as (endpoint, calls):
        created = await create_source(service, endpoint)
        item = f"{service.api}/metrics-data-sources/{created.json()['id']}"
        for query in [
            {"kind": "instant", "expression": "up", "time": 123},
            {"kind": "range", "expression": "sum(rate(x[5m]))", "start": 100, "end": 200, "step": 10},
        ]:
            response = await service.client.post(item + "/query", json=query)
            assert response.status_code == 200, response.text
            assert response.json()["data"] == RESULT["data"]
        assert calls[0][0] == "POST /gateway/api/v1/query HTTP/1.1"
        assert calls[0][2]["time"] == ["123.0"]
        assert calls[1][0] == "POST /gateway/api/v1/query_range HTTP/1.1"
        assert calls[1][2]["query"] == ["sum(rate(x[5m]))"]
        assert all(call[1]["authorization"] == "bearer query-fixture" for call in calls)
        assert "query-fixture" not in response.text
        changed = await service.client.patch(
            item, json={"enabled": False}, headers={"if-match": created.headers["etag"]}
        )
        assert changed.status_code == 200
        assert (
            await service.client.post(item + "/query", json={"kind": "instant", "expression": "up"})
        ).status_code == 422
        assert len(calls) == 2


async def test_custom_and_private_environment_queries_have_independent_authority(service):
    owner = await builder(service)
    other = await builder(service)
    workspace = service.tenant.workspace_id
    async with backend() as (endpoint, calls):
        created = await create_source(service, endpoint)
        source_id = created.json()["id"]
        target = await environment(service, source_id, owner.id)
        query = EnvironmentQuery(metric="system_cpu_busy_ratio", start=100, end=200, step=10)
        with pytest.raises(ServiceError) as error:
            await sources.query_source(service.runtime, owner, workspace, source_id, InstantQuery(expression="up"))
        assert error.value.code == "forbidden"
        with pytest.raises(ServiceError) as error:
            await query_environment(service.runtime, other, workspace, target, query)
        assert error.value.code == "forbidden"
        with pytest.raises(ServiceError):
            await query_environment(service.runtime, owner, new_object_id("ws"), target, query)
        assert not calls
        result = await query_environment(service.runtime, owner, workspace, target, query)
        assert result.data.result == []
        assert calls[0][2]["query"] == [
            expression("system_cpu_busy_ratio", {"device_id": 'device"\\\n', "tenant": "ours"})
        ]
        # The public typed route accepts a metric key, never a substituted arbitrary expression.
        response = await service.client.post(
            f"{service.api}/environments/{target}/metrics/query", json={**query.model_dump(), "expression": "up"}
        )
        assert response.status_code == 400
        assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (302, "unavailable"),
        (401, "unavailable"),
        (422, "invalid_argument"),
        (429, "rate_limited"),
        (503, "unavailable"),
    ],
)
async def test_query_failures_do_not_forward_upstream_details_or_redirects(status, code):
    async with backend(status=status, payload={"error": "credential-fixture"}) as (endpoint, calls):
        with pytest.raises(ServiceError) as error:
            await Queries().execute(EndpointPolicy(), Destination(endpoint), InstantQuery(expression="up"))
        assert error.value.code == code
        assert "credential-fixture" not in str(error.value)
        assert len(calls) == 1


@pytest.mark.parametrize(
    "payload,max_bytes,code",
    [
        ({"wrong": "body"}, 4096, "unavailable"),
        (RESULT, 4, "payload_too_large"),
        ({"status": "success", "data": {"resultType": "vector", "result": [{}] * 201}}, 4096, "payload_too_large"),
    ],
)
async def test_query_response_bounds(payload, max_bytes, code):
    async with backend(payload=payload) as (endpoint, _):
        with pytest.raises(ServiceError) as error:
            await Queries(max_bytes=max_bytes).execute(
                EndpointPolicy(), Destination(endpoint), InstantQuery(expression="up")
            )
        assert error.value.code == code


async def test_query_capacity_is_released_after_cancellation_and_timeout():
    arrived = asyncio.Event()
    release = asyncio.Event()

    async def blocked():
        arrived.set()
        await release.wait()

    async with backend(wait=blocked) as (endpoint, _):
        queries = Queries(concurrency=1, timeout=1)
        args = EndpointPolicy(), Destination(endpoint), InstantQuery(expression="up")
        running = asyncio.create_task(queries.execute(*args))
        await asyncio.wait_for(arrived.wait(), 2)
        with pytest.raises(ServiceError) as error:
            await queries.execute(*args)
        assert error.value.code == "rate_limited"
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert queries.slots.borrowed_tokens == 0
        with pytest.raises(ServiceError) as error:
            await queries.execute(*args)
        assert error.value.code == "request_timeout"
        assert queries.slots.borrowed_tokens == 0
        release.set()
        assert (await queries.execute(*args)).data.result == []


@pytest.mark.parametrize(
    "values",
    [
        {"start": 200, "end": 100, "step": 10},
        {"start": 0, "end": 11000, "step": 1},
        {"start": 0, "end": 91 * 86400, "step": 86400},
        {"start": 0, "end": 100, "step": 0},
        {"start": 0, "end": float("inf"), "step": 10},
    ],
)
def test_query_ranges_are_bounded(values):
    with pytest.raises(ValidationError):
        RangeQuery(expression="up", **values)


async def test_query_endpoint_policy_is_checked_before_network():
    from a13n_harness import RunConfiguration

    async with backend() as (endpoint, calls):
        policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"other.invalid"}))
        with pytest.raises(ServiceError) as error:
            await Queries().execute(policy, Destination(endpoint), InstantQuery(expression="up"))
        assert error.value.code == "unavailable"
        assert not calls


async def test_service_queries_real_prometheus(service):
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import HttpWaitStrategy

    container = DockerContainer("prom/prometheus:v3.2.1").with_exposed_ports(9090)
    container.waiting_for(HttpWaitStrategy(9090, "/-/ready").for_status_code(200))
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9090)}"
        source = await create_source(service, endpoint)
        item = f"{service.api}/metrics-data-sources/{source.json()['id']}"
        config = source.json()["config"]
        config["query"]["endpoint"] = endpoint
        changed = await service.client.patch(
            item, json={"config": config, "query_credentials": None}, headers={"if-match": source.headers["etag"]}
        )
        assert changed.status_code == 200, changed.text
        instant = await service.client.post(
            item + "/query", json={"kind": "instant", "expression": "1 + 2", "time": 100}
        )
        assert instant.status_code == 200, instant.text
        assert instant.json()["data"] == {"resultType": "scalar", "result": [100, "3"]}
        ranged = await service.client.post(
            item + "/query", json={"kind": "range", "expression": "vector(0.5)", "start": 100, "end": 120, "step": 10}
        )
        assert ranged.status_code == 200, ranged.text
        assert ranged.json()["data"] == {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": [[100, "0.5"], [110, "0.5"], [120, "0.5"]]}],
        }
        invalid = await service.client.post(item + "/query", json={"kind": "instant", "expression": "sum("})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_argument"


async def test_query_resolution_releases_database_before_http(runtime, tenant):
    from a13n_service.resources.metrics_data_sources.schemas import DataSourceCreate

    principal = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    checked = []

    async def assert_detached():
        # This fixture owns no sweeps: every checkout belongs to the query under test.
        checked.append(runtime.storage.engine.pool.checkedout())

    async with backend(wait=assert_detached) as (endpoint, _):
        source = await sources.create_source(
            runtime,
            principal,
            tenant.workspace_id,
            DataSourceCreate.model_validate(
                {"name": "query", "config": {"collection": "external", "query": {"endpoint": endpoint}}}
            ),
        )
        result = await sources.query_source(
            runtime, principal, tenant.workspace_id, source.id, InstantQuery(expression="up")
        )
        assert result.data.result == []
        assert checked == [0]


@pytest.mark.parametrize("collection", ["external", "proxy"])
async def test_external_identity_cannot_self_authorize_a_shared_source(service, collection):
    from a13n_service.runs.environments import service as environments
    from a13n_service.runs.environments.monitoring.bindings import Selection

    owner = await builder(service)
    workspace = service.tenant.workspace_id
    async with backend() as (endpoint, calls):
        created = await create_source(service, endpoint)
        source_id = created.json()["id"]
        if collection == "proxy":
            changed = await service.client.patch(
                f"{service.api}/metrics-data-sources/{source_id}",
                json={"config": {"collection": "proxy"}},
                headers={"if-match": created.headers["etag"]},
            )
            assert changed.status_code == 200
        # A second endpoint can self-report exactly the same device identity as a private target.
        victim = await environment(service, source_id, service.tenant.principal_id)
        impostor = await environment(service, None, owner.id)
        view = await environments.get_environment(service.runtime.storage, owner, workspace, impostor)
        with pytest.raises(ServiceError) as error:
            await environments.select_metrics_source(
                service.runtime,
                owner,
                workspace,
                impostor,
                Selection(metrics_data_source_id=source_id),
                if_match=f'"{view.id}:{view.version}"',
            )
        assert error.value.code == "forbidden"
        assert victim != impostor and not calls
        local = next(
            source
            for source in (
                await sources.list_sources(service.runtime.storage, owner, workspace, limit=10, cursor=None)
            ).items
            if source.builtin
        )
        selected = await environments.select_metrics_source(
            service.runtime,
            owner,
            workspace,
            impostor,
            Selection(metrics_data_source_id=local.id),
            if_match=f'"{view.id}:{view.version}"',
        )
        assert selected.metrics_data_source_id == local.id
        cleared = await environments.select_metrics_source(
            service.runtime,
            owner,
            workspace,
            impostor,
            Selection(metrics_data_source_id=None),
            if_match=f'"{selected.id}:{selected.version}"',
        )
        assert cleared.metrics_data_source_id is None


async def test_deleted_environment_can_release_its_source_reference(service):
    created = await create_source(service, "http://127.0.0.1:9")
    source_id = created.json()["id"]
    target = await environment(service, source_id, service.tenant.principal_id)
    item = f"{service.api}/environments/{target}"
    current = await service.client.get(item)
    deleted = await service.client.delete(item, headers={"if-match": current.headers["etag"]})
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["status"] == "deleted"
    source_url = f"{service.api}/metrics-data-sources/{source_id}"
    assert (await service.client.delete(source_url, headers={"if-match": created.headers["etag"]})).status_code == 409
    rebind = await service.client.put(
        item + "/metrics-binding",
        json={"metrics_data_source_id": source_id},
        headers={"if-match": deleted.headers["etag"]},
    )
    assert rebind.status_code == 409
    cleared = await service.client.put(
        item + "/metrics-binding", json={"metrics_data_source_id": None}, headers={"if-match": deleted.headers["etag"]}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "deleted" and cleared.json()["metrics_data_source_id"] is None
    assert (await service.client.delete(source_url, headers={"if-match": created.headers["etag"]})).status_code == 204
