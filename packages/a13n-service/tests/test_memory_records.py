"""Record memories: Memory Providers, provider namespaces, the records API, and namespace purges through the outbox."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.capabilities import DEFAULT_RECORD_GUIDE
from a13n_service.distribution import OSS
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbox import Delivery, OutboxRow, Policy
from a13n_service.providers.registry import Registry
from a13n_service.resources.memories import records
from a13n_service.resources.memories import service as memories
from a13n_service.resources.memories.purge import MemoryPurger
from a13n_service.resources.memories.schemas import MemoryCreate, MemoryRecordSearch, MemoryRecordText
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from sqlalchemy import func, select, update

from .records_support import BACKEND, create_record_memory, with_fake_records
from .test_providers import Replying, add_workspace, serving

pytestmark = pytest.mark.anyio


def etag(resource: dict[str, Any]) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


def error(response: Any) -> dict[str, Any]:
    return response.json()["error"]


def refused_field(response: Any) -> str:
    """The field a 400 names, whether the Service or request validation refused it."""
    details = error(response)["details"]
    return details["field"] if "field" in details else details["fields"][0]["field"].removeprefix("body.")


async def purges(service: SimpleNamespace) -> list[OutboxRow]:
    async with short_session(service.runtime.storage) as session:
        return list(
            await session.scalars(select(OutboxRow).where(OutboxRow.kind == "memory_purge").order_by(OutboxRow.id))
        )


async def deliver_purges(service: SimpleNamespace, *, max_attempts: int = 3, registry: Registry | None = None) -> None:
    """One pass of the outbox sweep over purges, with every pending purge due first."""
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(OutboxRow).where(OutboxRow.status == "pending").values(available_at=func.now()))
    runtime = service.runtime if registry is None else replace(service.runtime, registry=registry)
    await Delivery(
        runtime.storage,
        {"memory_purge": MemoryPurger(runtime)},
        owner="test",
        policies={"memory_purge": Policy(batch=10, max_attempts=max_attempts)},
    )()


async def test_memory_providers_describe_and_probe_their_backend(service) -> None:  # type: ignore[no-untyped-def]
    provider = await with_fake_records(service)
    assert provider["id"].startswith("memprov_") and provider["type"] == "fake_records"
    types = (await service.client.get("/api/v1/provider-types/memory")).json()["items"]
    assert {item["type"]: item["supports_test"] for item in types}["fake_records"] is True

    item = f"{service.api}/memory-providers/{provider['id']}"
    passed = (await service.client.post(item + "/test")).json()
    assert passed["status"] == "succeeded"
    # The probe lists one page of a namespace no memory owns.
    assert BACKEND.calls == [("list", "a13n-probe")]
    BACKEND.failing["list"] = "unavailable"
    failed = (await service.client.post(item + "/test")).json()
    assert (failed["status"], failed["message"]) == ("failed", "unavailable")


async def test_record_memories_own_a_provider_namespace(service) -> None:  # type: ignore[no-untyped-def]
    base = f"{service.api}/memories"
    provider = await with_fake_records(service)
    memory = await create_record_memory(service, provider)
    assert (memory["kind"], memory["type"], memory["provider_id"]) == ("record", "fake_records", provider["id"])
    assert memory["namespace"] == memories.default_namespace(memory["id"])
    assert len(memory["namespace"]) == len("a13n-") + 32
    assert (memory["file_count"], memory["content_bytes"], memory["history_bytes"]) == (None, None, None)
    assert memory["inherited_guide"] == DEFAULT_RECORD_GUIDE
    file_memory = (await service.client.post(base, json={"name": "notes"})).json()
    assert (file_memory["provider_id"], file_memory["namespace"]) == (None, None)

    # An explicit namespace adopts the records already in it, and no second memory may own it.
    team = "team:notes/été"
    BACKEND.seed(team, "The deploy window is Tuesday.")
    adopted = await create_record_memory(service, provider, "team", namespace=team)
    assert adopted["namespace"] == team
    listed = await service.client.get(f"{base}/{adopted['id']}/records")
    assert [item["text"] for item in listed.json()["items"]] == ["The deploy window is Tuesday."]
    duplicate = await service.client.post(
        base,
        json={"name": "Again", "type": "fake_records", "provider_id": provider["id"], "namespace": team},
    )
    assert duplicate.status_code == 409 and error(duplicate)["code"] == "already_exists"

    record = {"type": "fake_records", "provider_id": provider["id"]}
    for body, field in (
        ({**record, "always_load": ["a.md"]}, "always_load"),
        ({**record, "namespace": "has space"}, "namespace"),
        ({**record, "namespace": "user-*"}, "namespace"),
        ({**record, "namespace": "bell\u0007"}, "namespace"),
        ({**record, "namespace": "x" * 257}, "namespace"),
        ({**record, "type": "other"}, "type"),
        ({"type": "fake_records"}, "provider_id"),
        ({"provider_id": provider["id"]}, "provider_id"),
        ({"namespace": "mine"}, "namespace"),
    ):
        refused = await service.client.post(base, json={"name": "Bad", **body})
        assert refused.status_code == 400, refused.text
        assert refused_field(refused) == field
    # Type, provider and namespace are fixed once the memory exists.
    item = f"{base}/{memory['id']}"
    for field, value in (("type", "postgres"), ("provider_id", None), ("namespace", "other")):
        changed = await service.client.patch(item, json={field: value}, headers={"if-match": etag(memory)})
        assert changed.status_code == 400, changed.text

    async def listed_names(**params: str) -> list[str]:
        return sorted(item["name"] for item in (await service.client.get(base, params=params)).json()["items"])

    assert await listed_names(kind="record") == ["facts", "team"]
    assert await listed_names(type="postgres") == ["notes"]
    assert await listed_names(kind="file", type="fake_records") == []


async def test_a_record_memory_needs_a_usable_enabled_provider(service) -> None:  # type: ignore[no-untyped-def]
    base = f"{service.api}/memories"
    provider = await with_fake_records(service)
    elsewhere = await service.client.post(
        f"{service.api}/memory-providers",
        json={"type": "fake_records", "name": "Elsewhere", "config": {}},
        headers={"x-workspace-id": await add_workspace(service)},
    )
    assert elsewhere.status_code == 201, elsewhere.text
    foreign = await service.client.post(
        base, json={"name": "X", "type": "fake_records", "provider_id": elsewhere.json()["id"]}
    )
    assert foreign.status_code == 404 and error(foreign)["code"] == "not_found"

    memory = await create_record_memory(service, provider)
    provider_item = f"{service.api}/memory-providers/{provider['id']}"
    disabled = await service.client.patch(provider_item, json={"enabled": False}, headers={"if-match": etag(provider)})
    assert disabled.status_code == 200, disabled.text
    refused = await service.client.post(base, json={"name": "Y", "type": "fake_records", "provider_id": provider["id"]})
    assert refused.status_code == 422 and error(refused)["code"] == "disabled"
    # A disabled provider also stops the records API of the memories that use it.
    listed = await service.client.get(f"{base}/{memory['id']}/records")
    assert listed.status_code == 422 and error(listed)["code"] == "disabled"


async def test_records_are_listed_searched_and_edited_in_the_backend(service) -> None:  # type: ignore[no-untyped-def]
    provider = await with_fake_records(service)
    memory = await create_record_memory(service, provider)
    collection = f"{service.api}/memories/{memory['id']}/records"
    added = []
    for text in ("The user prefers dark mode.", "Deploys happen on Tuesday.", "The user lives in Lisbon."):
        response = await service.client.post(collection, json={"text": text})
        assert response.status_code == 201, response.text
        added.append(response.json())
    assert added[0]["text"] == "The user prefers dark mode." and added[0]["id"]
    assert BACKEND.texts(memory["namespace"]) == [item["text"] for item in added]

    first = (await service.client.get(collection, params={"limit": 2})).json()
    second = (await service.client.get(collection, params={"limit": 2, "cursor": first["next_cursor"]})).json()
    assert [item["id"] for item in first["items"] + second["items"]] == [item["id"] for item in added]
    assert second["next_cursor"] is None

    found = (await service.client.post(collection + "/search", json={"query": "Is it Lisbon?"})).json()
    assert found["items"][0]["id"] == added[2]["id"] and found["items"][0]["score"] > 0
    assert found["next_cursor"] is None

    record = f"{collection}/{added[1]['id']}"
    updated = await service.client.put(record, json={"text": "Deploys happen on Wednesday."})
    assert updated.status_code == 200 and updated.json()["text"] == "Deploys happen on Wednesday."
    assert (await service.client.delete(record)).status_code == 204
    missing = await service.client.put(record, json={"text": "Gone."})
    assert missing.status_code == 404 and error(missing)["details"] == {"kind": "memory_record", "id": added[1]["id"]}
    # A record of another namespace is not this memory's.
    [foreign] = BACKEND.seed("someone-else", "Not yours.")
    assert (await service.client.delete(f"{collection}/{foreign}")).status_code == 404
    assert BACKEND.texts("someone-else") == ["Not yours."]

    # Writes are audited by record ID, never by text.
    async with short_session(service.runtime.storage) as session:
        audit = (
            await session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.target_id == memory["id"], AuditEventRow.action.like("memory.record.%"))
                .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
            )
        ).all()
    assert sorted((event.action, event.details["record_id"]) for event in audit) == sorted(
        [
            *(("memory.record.create", item["id"]) for item in added),
            ("memory.record.update", added[1]["id"]),
            ("memory.record.delete", added[1]["id"]),
        ]
    )
    assert all(set(event.details) == {"record_id"} for event in audit)


async def test_backend_refusals_map_to_service_errors(service) -> None:  # type: ignore[no-untyped-def]
    provider = await with_fake_records(service)
    memory = await create_record_memory(service, provider)
    collection = f"{service.api}/memories/{memory['id']}/records"

    BACKEND.failing["add"] = "write_unconfirmed"
    unconfirmed = await service.client.post(collection, json={"text": "Maybe written."})
    assert unconfirmed.status_code == 409 and error(unconfirmed)["details"]["reason"] == "write_unconfirmed"
    BACKEND.failing["add"] = "invalid_text"
    invalid = await service.client.post(collection, json={"text": "Refused upstream."})
    assert invalid.status_code == 400 and refused_field(invalid) == "text"
    BACKEND.failing["list"] = "invalid_cursor"
    stale = await service.client.get(collection, params={"cursor": "not-issued"})
    assert stale.status_code == 400 and refused_field(stale) == "cursor"
    BACKEND.failing["search"] = "unavailable"
    down = await service.client.post(collection + "/search", json={"query": "anything"})
    assert down.status_code == 503 and error(down)["details"] == {"dependency": "memory:fake_records"}
    for blank in ("", "   "):
        assert (await service.client.post(collection, json={"text": blank})).status_code == 400
    assert (await service.client.post(collection, json={"text": "x" * 8001})).status_code == 400

    # The deployment's record limit applies before the backend is called.
    BACKEND.reset()
    limited = replace(
        service.runtime,
        settings=service.runtime.settings.model_copy(
            update={"memory": service.runtime.settings.memory.model_copy(update={"record_chars": 10})}
        ),
    )
    actor = Principal(service.tenant.principal_id, "user", ())
    with pytest.raises(ServiceError) as long:
        await records.add_record(
            limited, actor, service.tenant.workspace_id, memory["id"], MemoryRecordText(text="x" * 11)
        )
    assert (long.value.code, long.value.details["field"], BACKEND.calls) == ("invalid_argument", "text", [])

    # File routes refuse a record memory, and record routes a file memory.
    file_memory = (await service.client.post(f"{service.api}/memories", json={"name": "N"})).json()
    for path in (f"{memory['id']}/files", f"{file_memory['id']}/records"):
        crossed = await service.client.get(f"{service.api}/memories/{path}")
        assert crossed.status_code == 409 and error(crossed)["details"]["reason"] == "memory_kind"


async def test_record_verbs_follow_the_memory_rules(service) -> None:  # type: ignore[no-untyped-def]
    runtime, tenant = service.runtime, service.tenant
    provider = await with_fake_records(service)
    runtime = service.runtime

    def member(role: str) -> Principal:
        return Principal(
            tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES[role]),)
        )

    viewer, runner, builder = member("viewer"), member("runner"), member("builder")
    create = MemoryCreate(name="Facts", type="fake_records", provider_id=provider["id"])
    for actor in (viewer, runner):
        with pytest.raises(ServiceError) as refused:
            await memories.create_memory(
                runtime.storage, actor, tenant.workspace_id, create, settings=runtime.settings.memory
            )
        assert refused.value.code == "forbidden"
    memory = await memories.create_memory(
        runtime.storage, builder, tenant.workspace_id, create, settings=runtime.settings.memory
    )
    workspace_id, text = tenant.workspace_id, MemoryRecordText(text="The user prefers tea.")

    with pytest.raises(ServiceError) as viewing:
        await records.add_record(runtime, viewer, workspace_id, memory.id, text)
    assert viewing.value.code == "forbidden"
    added = await records.add_record(runtime, runner, workspace_id, memory.id, text)
    await records.update_record(runtime, runner, workspace_id, memory.id, added.id, MemoryRecordText(text="Coffee."))
    for write in (
        records.update_record(runtime, viewer, workspace_id, memory.id, added.id, text),
        records.delete_record(runtime, viewer, workspace_id, memory.id, added.id),
    ):
        with pytest.raises(ServiceError) as refused:
            await write
        assert refused.value.code == "forbidden"
    page = await records.list_records(runtime, viewer, workspace_id, memory.id, limit=10, cursor=None)
    found = await records.search_records(runtime, viewer, workspace_id, memory.id, MemoryRecordSearch(query="coffee"))
    assert [item.text for item in page.items] == [item.text for item in found.items] == ["Coffee."]
    await records.delete_record(runtime, runner, workspace_id, memory.id, added.id)
    assert BACKEND.texts(memory.namespace or "") == []


async def test_deleting_a_record_memory_purges_its_namespace(service, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    base = f"{service.api}/memories"
    provider = await with_fake_records(service)
    memory = await create_record_memory(service, provider, namespace="shared-notes")
    BACKEND.seed("shared-notes", "Kept until the purge.")
    BACKEND.seed("other", "Never purged.")
    assert (
        await service.client.delete(f"{base}/{memory['id']}", headers={"if-match": etag(memory)})
    ).status_code == 204
    [pending] = await purges(service)
    assert (pending.status, pending.dedupe_key) == ("pending", memory["id"])
    assert pending.target == {"provider_id": provider["id"], "namespace": "shared-notes"}

    # Until the purge settles, no memory may claim the namespace.
    adopt = {"name": "Again", "type": "fake_records", "provider_id": provider["id"]}
    purging = await service.client.post(base, json={**adopt, "namespace": "shared-notes"})
    assert purging.status_code == 409, purging.text
    assert error(purging)["details"] == {
        "kind": "memory_provider",
        "id": provider["id"],
        "reason": "namespace_purging",
        "namespace": "shared-notes",
    }

    BACKEND.failing["purge"] = "unavailable"
    await deliver_purges(service)
    [retrying] = await purges(service)
    assert (retrying.status, retrying.attempts, retrying.last_error) == ("pending", 1, "unavailable")
    assert BACKEND.texts("shared-notes") == ["Kept until the purge."]
    del BACKEND.failing["purge"]
    await deliver_purges(service)
    [delivered] = await purges(service)
    assert delivered.status == "delivered"
    assert (BACKEND.texts("shared-notes"), BACKEND.texts("other")) == ([], ["Never purged."])
    assert (await service.client.post(base, json={**adopt, "namespace": "shared-notes"})).status_code == 201


async def test_a_purge_that_cannot_finish_ends_dead(service, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    provider = await with_fake_records(service)

    async def deleted(name: str) -> dict[str, Any]:
        memory = await create_record_memory(service, provider, name)
        item = f"{service.api}/memories/{memory['id']}"
        assert (await service.client.delete(item, headers={"if-match": etag(memory)})).status_code == 204
        return memory

    # A deployment that no longer offers the provider's type cannot purge: dead at once, without a call.
    orphaned = await deleted("orphaned")
    await deliver_purges(service, registry=Registry.of(OSS.providers))
    failing = await deleted("failing")
    BACKEND.failing["purge"] = "unavailable"
    await deliver_purges(service, max_attempts=1)
    outcomes = {row.dedupe_key: (row.status, row.attempts, row.last_error) for row in await purges(service)}
    assert outcomes == {orphaned["id"]: ("dead", 1, "type_unavailable"), failing["id"]: ("dead", 1, "unavailable")}
    assert BACKEND.calls == [("purge", failing["namespace"])]


async def test_a_backend_answer_over_the_byte_bound_is_a_classified_failure(service, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """The Service's transport cuts off an answer over `providers.response_bytes` below the mem0 store's own bound; a
    read then reports the backend unavailable, and a write may have happened."""
    await runs_kit.pause_sweeps(service)
    settings = service.runtime.settings
    providers = settings.providers.model_copy(update={"response_bytes": 65536})
    service.runtime = replace(service.runtime, settings=settings.model_copy(update={"providers": providers}))
    service.app.state.runtime = service.runtime

    class Oversized(Replying):
        def answer(self) -> None:
            self.reply(200, b'{"results": [], "padding": "' + b"x" * 100_000 + b'"}')

        do_GET = do_POST = do_DELETE = answer

    with serving(Oversized) as port:
        body = {"type": "mem0_oss", "name": "mem0", "config": {"base_url": f"http://127.0.0.1:{port}"}}
        provider = (await service.client.post(f"{service.api}/memory-providers", json=body)).json()
        memory = (
            await service.client.post(
                f"{service.api}/memories",
                json={"name": "Facts", "type": "mem0_oss", "provider_id": provider["id"]},
            )
        ).json()
        collection = f"{service.api}/memories/{memory['id']}/records"
        listed = await service.client.get(collection)
        assert listed.status_code == 503 and error(listed)["details"] == {"dependency": "memory:mem0_oss"}
        added = await service.client.post(collection, json={"text": "likes green tea"})
        assert added.status_code == 409 and error(added)["details"]["reason"] == "write_unconfirmed"

        assert (
            await service.client.delete(f"{service.api}/memories/{memory['id']}", headers={"if-match": etag(memory)})
        ).status_code == 204
        await deliver_purges(service)
        [purge] = await purges(service)
        assert (purge.status, purge.last_error) == ("pending", "unavailable")
