"""Runs with mounted record memories: recall at a run's first input, the record tools a mount offers, memories a
run skips, and the per-call recheck of the run, the memory and its provider."""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from a13n_harness.providers.memory import MemoryStoreError
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import Lease
from a13n_service.runs.memories.execution import record_memory_capability, resolve_memories
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal

from .records_support import BACKEND, create_record_memory, with_fake_records
from .test_runs_memory import text, tool_names, tool_result

pytestmark = pytest.mark.anyio

RECALL = '<memory-recall memory="facts" trust="untrusted">'
RECORD_TOOLS = {f"memory_record_{key}" for key in ("search", "list", "add", "update", "delete")}


async def facts(service: SimpleNamespace, *texts: str) -> dict[str, Any]:
    """A record memory of the fake provider holding `texts`."""
    memory = await create_record_memory(service, await with_fake_records(service))
    BACKEND.seed(memory["namespace"], *texts)
    return memory


def mount(memory: dict[str, Any], access: str = "write", **fields: Any) -> dict[str, Any]:
    return {"name": "facts", "memory_id": memory["id"], "access": access, **fields}


def memory_tools(request: dict[str, Any]) -> set[str]:
    return {name for name in tool_names(request) if name.startswith("memory_")}


async def test_a_run_recalls_at_its_first_input_and_writes_through_the_tools(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await facts(service, "likes green tea", "lives in Lisbon")
    namespace = memory["namespace"]
    agent = await runs_kit.create_agent(service, scripted_model)
    add = {"memory": "facts", "text": "prefers window seats"}
    scripted_model.call("memory_record_add", add, call_id="call_add")
    scripted_model.say("Noted")
    started = await runs_kit.start_thread(service, agent, "Which tea do I like?", memories=[mount(memory)])
    await (await runs_kit.attempt(service))

    first, second = await scripted_model.request(), await scripted_model.request()
    recalled = text(first)
    assert RECALL in recalled and "likes green tea" in recalled and "lives in Lisbon" not in recalled
    assert memory_tools(first) == RECORD_TOOLS
    # The recalled records are part of the input's history; the tool results' request recalls nothing new.
    assert text(second).count(RECALL) == 1
    assert '"id"' in tool_result(second, "call_add")
    assert "prefers window seats" in BACKEND.texts(namespace)
    assert [call for call in BACKEND.calls if call[0] != "search"] == [("add", namespace)]
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"


async def test_a_mount_and_the_toolset_limit_recall_and_tools(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await facts(service, "likes green tea")
    model_id = await runs_kit.create_model(service, scripted_model)
    no_list = {"memory": {"tools": {"record_list": {"enabled": False}}}}
    reader = await runs_kit.add_agent(service, "reader", model_id, toolsets=no_list)
    scripted_model.say("Read")
    await runs_kit.start_thread(service, reader, "Which tea?", memories=[mount(memory, "read", recall=False)])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert RECALL not in text(request) and memory_tools(request) == {"memory_record_search"}
    assert BACKEND.calls == []

    # Without the toolset the run still recalls, with no tool.
    silent = await runs_kit.add_agent(service, "silent", model_id, toolsets={"memory": {"enabled": False}})
    scripted_model.say("Quiet")
    await runs_kit.start_thread(service, silent, "Which tea?", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert RECALL in text(request) and memory_tools(request) == set()


async def test_a_failing_recall_or_an_unusable_memory_does_not_fail_the_run(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await facts(service, "likes green tea")
    agent = await runs_kit.create_agent(service, scripted_model)
    BACKEND.failing["search"] = "unavailable"
    scripted_model.say("No recall")
    started = await runs_kit.start_thread(service, agent, "Which tea?", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert RECALL not in text(request) and memory_tools(request) == RECORD_TOOLS
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"

    # A disabled provider leaves the memory out of the run altogether.
    provider_item = f"{service.organization}/memory-providers/{memory['provider_id']}"
    provider = (await service.client.get(provider_item)).json()
    disabled = await service.client.patch(provider_item, json={"enabled": False}, headers=runs_kit.if_match(provider))
    assert disabled.status_code == 200, disabled.text
    scripted_model.say("No memory")
    started = await runs_kit.start_thread(service, agent, "Which tea?", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert RECALL not in text(request) and memory_tools(request) == set()
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"


async def test_a_transport_failure_reaches_the_model_as_a_store_error(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    memory = await facts(service, "likes green tea")
    agent = await runs_kit.create_agent(service, scripted_model)
    # A refused connection, and an answer the Service's transport cuts off at `providers.response_bytes`.
    BACKEND.failing.update(
        search=httpx2.ConnectError("refused"), add=ServiceError("payload_too_large", "The answer is too large.")
    )
    scripted_model.call("memory_record_add", {"memory": "facts", "text": "prefers window seats"}, call_id="call_add")
    scripted_model.call("memory_record_search", {"memory": "facts", "query": "tea"}, call_id="call_search")
    scripted_model.say("Done")
    started = await runs_kit.start_thread(service, agent, "Which tea?", memories=[mount(memory)])
    await (await runs_kit.attempt(service))
    first, second, third = [await scripted_model.request() for _ in range(3)]
    assert RECALL not in text(first)
    assert "write_unconfirmed" in tool_result(second, "call_add")
    assert "unavailable" in tool_result(third, "call_search")
    assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"


async def test_a_record_memory_deleted_mid_run_refuses_its_next_call(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    memory = await facts(service, "likes green tea")
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.call("memory_record_list", {"memory": "facts"}, call_id="call_list", gate=gate)
    scripted_model.say("Gone")
    await runs_kit.start_thread(service, agent, "list them", memories=[mount(memory)])
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    deleted = await service.client.delete(
        f"{service.workspace}/memories/{memory['id']}", headers=runs_kit.if_match(memory)
    )
    assert deleted.status_code == 204, deleted.text
    gate.set()
    await running
    assert "memory_deleted" in tool_result(await scripted_model.request(), "call_list")
    assert [call[0] for call in BACKEND.calls] == ["search"]


async def test_record_store_calls_recheck_the_run_and_the_provider(service) -> None:  # type: ignore[no-untyped-def]
    memory = await facts(service, "likes green tea")
    tenant = service.tenant
    viewer = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"]),)
    )
    authority = ExecutionAuthority(
        principal_id=tenant.principal_id,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        verbs=frozenset({"read", "run"}),
    )
    run = RunRow(
        id="run_0123456789abcdef0123456789",
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        principal_id=tenant.principal_id,
        memory_mounts=[mount(memory)],
    )
    async with short_session(service.runtime.storage) as session:
        planned = await resolve_memories(session, run)
    async with record_memory_capability(
        service.runtime,
        planned,
        lease=Lease(run.id, "rat_test", "thr_test", run.organization_id, run.workspace_id, 1, "worker", "token"),
        principal=viewer,
        authority=authority,
        tools=("search", "add"),
    ) as capability:
        assert capability is not None
        [store] = [item.store for item in capability.mounts]
        assert [record.text for record in await store.search("green tea", limit=5)] == ["likes green tea"]
        with pytest.raises(MemoryStoreError) as refused:
            await store.add("prefers window seats")
        assert refused.value.code == "forbidden"

        provider_item = f"{service.organization}/memory-providers/{memory['provider_id']}"
        provider = (await service.client.get(provider_item)).json()
        headers = {"if-match": f'"{provider["id"]}:{provider["version"]}"'}
        assert (await service.client.patch(provider_item, json={"enabled": False}, headers=headers)).status_code == 200
        with pytest.raises(MemoryStoreError) as disabled:
            await store.search("green tea", limit=5)
        assert disabled.value.code == "unavailable"
    assert BACKEND.calls == [("search", memory["namespace"])]
