"""Accepted request context survives execution without becoming model-visible content."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from a13n_service.runs.inbox import steers_into
from a13n_service.runs.schemas import RequestContext, RunOptions, canonical_json
from a13n_service.runs.tables import InboxEntryRow, RunRow
from pydantic import ValidationError

from .mcp_support import call, client, key
from .test_request_overrides import resources
from .test_schedules import configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "private"},
        {"X-API-Key": "private"},
        {"Mcp-Session-Id": "private"},
        {"X-One": "a", "x-one": "b"},
        {"x-trace": "line\nbreak"},
        {"x-trace": "x" * 4097},
    ],
)
def test_context_headers_are_bounded_business_headers(headers: dict) -> None:
    with pytest.raises(ValidationError):
        RequestContext(headers=headers)


def test_context_omission_preserves_legacy_serialization_and_steer_digest() -> None:
    legacy = {"labels": {}, "configuration": None, "max_usage": None, "overrides": None}
    assert RunOptions().model_dump(mode="json") == legacy
    assert (
        RunOptions().digest()
        == hashlib.sha256(canonical_json({k: v for k, v in legacy.items() if k != "configuration"})).hexdigest()
    )
    headers = {"X-Trace": "original"}
    context = RequestContext(headers=headers)
    headers["X-Trace"] = "changed"
    assert context.headers == {"x-trace": "original"}
    assert "original" not in repr(context)


@pytest.mark.parametrize(
    "headers,joins", [(None, True), ({"X-Trace": "one"}, True), ({"x-trace": "two"}, False), ({}, False)]
)
def test_steer_retains_or_matches_context(headers: dict | None, joins: bool) -> None:
    accepted = RunOptions(context=RequestContext(headers={"x-trace": "one"}))
    run = RunRow(
        agent_id="agt_one",
        agent_revision_id="rev_one",
        options=accepted.model_dump(mode="json"),
        options_digest=accepted.digest(),
    )
    options = RunOptions(context=RequestContext(headers=headers)) if headers is not None else RunOptions()
    entry = InboxEntryRow(kind="message", delivery="steer", agent_id="agt_one", options=options.model_dump(mode="json"))
    assert steers_into(entry, run, None) is joins


@pytest.mark.parametrize("category,target", [(True, True), (False, True), (True, False), (True, None)])
@pytest.mark.parametrize("default", [False, True])
async def test_model_forwarding_priority_flags_and_fresh_runs(
    service: Any,
    scripted_model: Any,
    runs_kit: Any,
    monkeypatch: pytest.MonkeyPatch,
    category: bool,
    target: bool | None,
    default: bool,
) -> None:
    monkeypatch.setenv("A13N_HARNESS_FORWARD_CONTEXT_HEADERS", str(default).lower())
    captured: list[dict] = []

    @scripted_model.app.middleware("http")
    async def capture(request: Any, call_next: Any) -> Any:
        captured.append(dict(request.headers))
        return await call_next(request)

    provider, _, agent = await resources(service, scripted_model, runs_kit)
    updated = await service.client.patch(
        f"{service.api}/model-providers/{provider['id']}",
        json={"config": {**provider["config"], **({"forward_context_headers": target} if target is not None else {})}},
        headers=runs_kit.if_match(provider),
    )
    assert updated.status_code == 200, updated.text
    if target is not None:
        assert updated.json()["config"]["forward_context_headers"] is target
        monkeypatch.setenv("A13N_HARNESS_FORWARD_CONTEXT_HEADERS", str(not default).lower())
    settings = service.runtime.settings
    service.runtime = replace(
        service.runtime,
        settings=settings.model_copy(
            update={"providers": settings.providers.model_copy(update={"forward_model_context_headers": category})}
        ),
    )
    context = {
        "headers": {
            "X-Trace": "business-value",
            "X-Model": "lower-priority",
            "X-Gateway-Key": "cannot-replace",
            "X-Thread": "cannot-replace",
        }
    }
    scripted_model.say("Done")
    started = await runs_kit.start_thread(service, agent, "hi", options={"context": context})
    assert started["run"]["options"]["context"]["headers"]["x-trace"] == "business-value"
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, started["run"]["id"])
    assert run["status"] == "completed", run
    wire = captured[-1]
    assert wire.get("x-trace") == ("business-value" if category and (default if target is None else target) else None)
    assert wire["x-model"] == "default" and wire["x-gateway-key"] == "private"
    assert wire["authorization"] == "Bearer test-key" and wire["x-thread"] != "cannot-replace"
    assert "business-value" not in json.dumps(await scripted_model.request())
    scripted_model.say("Fresh")
    response = await service.client.post(
        f"{service.api}/threads/{run['thread_id']}/inbox",
        json=runs_kit.message(agent, "next"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    await (await runs_kit.attempt(service))
    assert captured[-1].get("x-trace") == (
        "business-value" if category and (default if target is None else target) else None
    )
    assert response.json()["run"]["options"]["context"]["headers"]["x-trace"] == "business-value"


async def test_mcp_run_inbox_and_schedule_reads_project_context_without_changing_storage(
    service: Any, scripted_model: Any, runs_kit: Any
) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    context = {"headers": {"x-trace": "private-context-value"}}
    started = await runs_kit.start_thread(service, agent, "hi", options={"context": context})
    run_id, thread_id, entry_id = started["run"]["id"], started["thread"]["id"], started["entry"]["id"]
    async with client(service, await key(service)) as http:
        for path, arguments in [
            ("/runs/{run_id}", {"run_id": run_id}),
            ("/runs/{run_id}", {"run_id": run_id, "section": "options"}),
            ("/threads/{thread_id}/inbox", {"thread_id": thread_id}),
            ("/threads/{thread_id}/inbox/{entry_id}", {"thread_id": thread_id, "entry_id": entry_id}),
            (
                "/threads/{thread_id}/inbox/{entry_id}",
                {"thread_id": thread_id, "entry_id": entry_id, "section": "options", "query": "private-context-value"},
            ),
        ]:
            result = await call(http, service, "GET", path, **arguments)
            assert result["status"] == 200, result
            if "query" in arguments:
                assert result["body"]["match_offset"] is None
                assert "private-context-value" not in result["body"]["text"]
            else:
                assert "private-context-value" not in json.dumps(result)
        body = configuration(agent["id"])
        body["invocation"]["options"] = {"context": context}
        body["invocation"]["payload"] = {"content": [{"type": "json", "value": {"context": "business-data"}}]}
        created = await call(http, service, "POST", "/schedules", request_body=body, idempotency_key="context-schedule")
        assert created["status"] == 201, created
        assert "private-context-value" not in json.dumps(created)
        assert "business-data" in json.dumps(created)
        schedule = created["body"]
        changed = await call(
            http,
            service,
            "PATCH",
            "/schedules/{schedule_id}",
            schedule_id=schedule["id"],
            request_body={"name": "Renamed"},
            expected_version=created["headers"]["etag"],
        )
        assert changed["status"] == 200, changed
        for path, args in [("/schedules", {}), ("/schedules/{schedule_id}", {"schedule_id": schedule["id"]})]:
            result = await call(http, service, "GET", path, **args)
            assert result["status"] == 200 and "private-context-value" not in json.dumps(result)
        stored = (await service.client.get(f"{service.api}/schedules/{schedule['id']}")).json()
        assert stored["invocation"]["options"]["context"] == context
        dispatched = await call(
            http,
            service,
            "POST",
            "/schedules/{schedule_id}/dispatch",
            schedule_id=schedule["id"],
            request_body={},
            idempotency_key="context-dispatch",
            expected_version=changed["headers"]["etag"],
        )
        assert dispatched["status"] == 202, dispatched
        assert "private-context-value" not in json.dumps(dispatched)
        occurrence = await call(
            http,
            service,
            "GET",
            "/schedules/{schedule_id}/occurrences/{occurrence_id}",
            schedule_id=schedule["id"],
            occurrence_id=dispatched["body"]["id"],
        )
        assert occurrence["status"] == 200 and "private-context-value" not in json.dumps(occurrence)
    assert (await runs_kit.get_run(service, run_id))["options"]["context"] == context


@pytest.mark.parametrize("latest", [{"headers": {"x-trace": "two"}}, {"headers": {}}])
async def test_new_runs_keep_the_latest_context_not_the_tool_origin(
    service: Any, scripted_model: Any, runs_kit: Any, latest: dict
) -> None:
    from a13n_service.infra.db import transaction
    from a13n_service.runs import accept, inbox
    from a13n_service.runs.tables import ThreadRow
    from a13n_service.runs.threads import lock_thread

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    first_context = {"headers": {"x-trace": "one", "x-removed": "original-only"}}
    scripted_model.say("First")
    first = await runs_kit.start_thread(service, agent, "first", options={"context": first_context})
    await (await runs_kit.attempt(service))
    thread_id = first["thread"]["id"]
    scripted_model.say("Second")
    second = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "replace", options={"context": latest}))
    assert second.status_code == 201, second.text
    await (await runs_kit.attempt(service))
    # A delayed result from Run A must use B's context when it admits Run C.
    async with transaction(service.runtime.storage) as session:
        thread = await lock_thread(session, thread_id)
        origin = await session.get_one(RunRow, first["run"]["id"])
        await inbox.append_tool_result(session, thread, origin, {"execution_id": "exec-delayed"})
        awakened = await accept.accept(session, service.runtime, thread)
        assert awakened is not None
        awakened_id = awakened.id
        assert awakened.parent_run_id == second.json()["run"]["id"]
        assert awakened.options["context"] == latest
        assert origin.options["context"] == first_context
    scripted_model.say("Awakened")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, awakened_id))["status"] == "completed"
    # Omission after a replacement or explicit clear keeps the accepted head.
    third = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "omitted"))
    assert third.status_code == 201, third.text
    assert third.json()["run"]["options"]["context"] == latest
    async with transaction(service.runtime.storage) as session:
        thread = await session.get_one(ThreadRow, thread_id)
        assert thread.current_run_id == third.json()["run"]["id"]


async def test_delayed_child_result_uses_latest_parent_context(
    service: Any, scripted_model: Any, runs_kit: Any
) -> None:
    from .test_runs_children import DELEGATE, _child_run, _deliver

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.delegating(service, scripted_model, "async")
    first_context = {"headers": {"x-trace": "one"}}
    latest = {"headers": {"x-trace": "two"}}
    scripted_model.call("delegate", DELEGATE, call_id="call_d", to="Role: coordinator")
    scripted_model.say("Delegated", to="Role: coordinator")
    first = await runs_kit.start_thread(service, agent, "delegate", options={"context": first_context})
    await (await runs_kit.attempt(service))
    child = await _child_run(service, first["run"]["id"])
    assert child.options["context"] == first_context
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    # Keep delivery queued while a newer external input changes the parent context.
    scripted_model.say("Updated", to="Role: coordinator")
    second = await runs_kit.submit(
        service, first["thread"]["id"], runs_kit.message(agent, "replace", options={"context": latest})
    )
    assert second.status_code == 201, second.text
    await (await runs_kit.attempt(service))
    assert len(await _deliver(service)) == 1
    thread = await runs_kit.get_thread(service, first["thread"]["id"])
    awakened = await runs_kit.get_run(service, thread["current_run_id"])
    assert awakened["trigger"] == "child_result"
    assert awakened["parent_run_id"] == second.json()["run"]["id"]
    assert awakened["options"]["context"] == latest
    assert (await runs_kit.get_run(service, child.id))["options"]["context"] == first_context
    scripted_model.say("Received", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, awakened["id"]))["status"] == "completed"


@pytest.mark.parametrize("kind", ["tool_result", "child_result"])
def test_completion_steers_a_newer_active_run_without_rebinding_context(kind: str) -> None:
    original = RunOptions(context=RequestContext(headers={"x-trace": "one"}))
    latest = RunOptions(context=RequestContext(headers={"x-trace": "two"}))
    origin = RunRow(id="run_origin", options=original.model_dump(mode="json"), sealed_at=datetime.now(UTC))
    active = RunRow(id="run_active", options=latest.model_dump(mode="json"))
    completion = InboxEntryRow(kind=kind, delivery="steer", origin_run_id=origin.id, options={})
    assert steers_into(completion, active, origin)
    assert active.options["context"] == {"headers": {"x-trace": "two"}}
    assert origin.options["context"] == {"headers": {"x-trace": "one"}}
