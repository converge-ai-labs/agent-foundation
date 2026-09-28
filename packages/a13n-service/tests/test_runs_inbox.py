"""The inbox: acceptance of queued input, capacity, pending edits by their submitter, order, withdrawal, archive."""

import asyncio
from dataclasses import replace

import pytest
from a13n_service.infra.db import lock, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.runs import inbox
from a13n_service.runs.accept import ThreadAdvancer
from a13n_service.runs.tables import RunRow, ThreadRow

pytestmark = pytest.mark.anyio


class _RefusingAgent:
    """Admission that refuses runs of one agent and admits every call."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def accept(self, session, intent) -> None:  # type: ignore[no-untyped-def]
        if intent.agent_id == self.agent_id:
            raise ServiceError("rate_limited", "No runs of this agent")

    async def proceed(self, session, call) -> None:  # type: ignore[no-untyped-def]
        return None


class _Unavailable:
    async def accept(self, session, intent) -> None:  # type: ignore[no-untyped-def]
        raise ServiceError("unavailable", "Admission is unavailable")

    async def proceed(self, session, call) -> None:  # type: ignore[no-untyped-def]
        return None


class _Breaking:
    """Admission that breaks while accepting runs of the named threads, as a poison thread does."""

    def __init__(self) -> None:
        self.threads: set[str] = set()

    async def accept(self, session, intent) -> None:  # type: ignore[no-untyped-def]
        if intent.thread_id in self.threads:
            raise RuntimeError("poisoned thread")

    async def proceed(self, session, call) -> None:  # type: ignore[no-untyped-def]
        return None


async def _queue(service, runs_kit, thread_id: str, agent: dict, text: str) -> dict:  # type: ignore[no-untyped-def]
    """Queue a message behind the thread's active run and return its entry."""
    response = await runs_kit.submit(service, thread_id, runs_kit.message(agent, text, delivery="next_run"))
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _dispositions(entries: list[dict]) -> list[tuple[str, str | None]]:
    return [(entry["status"], (entry["failure"] or {}).get("code")) for entry in entries]


async def test_a_refused_entry_fails_alone_and_the_next_one_starts(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    admitted = await runs_kit.add_agent(service, "admitted", model)
    refused = await runs_kit.add_agent(service, "refused", model)
    thread_id = (await runs_kit.start_thread(service, admitted, "first"))["thread"]["id"]
    await _queue(service, runs_kit, thread_id, refused, "refused")
    await _queue(service, runs_kit, thread_id, admitted, "second")

    # The first run's seal accepts its successor under an admission that refuses the next entry's agent.
    scripted_model.say("Done")
    await (await runs_kit.attempt(service, runtime=replace(service.runtime, admission=_RefusingAgent(refused["id"]))))
    entries = await runs_kit.inbox(service, thread_id)
    assert _dispositions(entries) == [("consumed", None), ("failed", "rate_limited"), ("assigned", None)]
    assert (await runs_kit.get_thread(service, thread_id))["current_run_id"] == entries[2]["assigned_run_id"]


async def test_an_unavailable_admission_stores_nothing(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    body, key = runs_kit.message(agent, "hi"), runs_kit.fresh_key()
    monkeypatch.setattr(service.app.state, "runtime", replace(service.runtime, admission=_Unavailable()))
    refused = await service.client.post(f"{service.api}/threads", json=body, headers=key)
    assert refused.status_code == 503, refused.text
    assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []

    monkeypatch.undo()
    retried = await service.client.post(f"{service.api}/threads", json=body, headers=key)
    assert retried.status_code == 201 and retried.json()["run"]["status"] == "accepted", retried.text


async def test_the_advance_sweep_passes_a_thread_that_breaks(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    policy = _Breaking()
    runtime = replace(service.runtime, admission=policy)
    for text in ("one", "two"):
        thread_id = (await runs_kit.start_thread(service, agent, text))["thread"]["id"]
        await _queue(service, runs_kit, thread_id, agent, "next")
        policy.threads.add(thread_id)
    # Each seal's own advance breaks, which leaves both threads idle with pending input.
    for _ in range(2):
        scripted_model.say("Done")
        await (await runs_kit.attempt(service, runtime=runtime))

    broken, healthy = sorted(policy.threads)
    policy.threads.discard(healthy)
    await ThreadAdvancer(runtime, batch=10)()
    assert (await runs_kit.get_thread(service, broken))["current_run_id"] is None
    assert (await runs_kit.get_thread(service, healthy))["current_run_id"] is not None


async def test_only_the_submitter_edits_a_pending_entry(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    thread_id = (await runs_kit.start_thread(service, agent, "first"))["thread"]["id"]
    queued = await _queue(service, runs_kit, thread_id, agent, "queued")
    accounts = f"{service.workspace}/service-accounts"
    account = (await service.client.post(accounts, json={"name": "runner", "role": "runner"})).json()
    key = await service.client.post(f"{accounts}/{account['id']}/keys", json={"name": "runner"})
    runner = {"authorization": "Bearer " + key.json()["secret"]}
    entry = f"{service.api}/threads/{thread_id}/inbox/{queued['id']}"
    edit = {"payload": {"content": [{"type": "text", "text": "rewritten"}]}}

    thread = runs_kit.if_match(await runs_kit.get_thread(service, thread_id))
    refused = await service.client.patch(entry, json=edit, headers={**runner, **thread})
    assert refused.status_code == 403, refused.text
    edited = await service.client.patch(entry, json=edit, headers=thread)
    assert edited.status_code == 200, edited.text
    assert edited.json()["entry"]["payload"] == edit["payload"]
    # Withdrawal stays open to every member who may run.
    withdrawn = await service.client.delete(entry, headers={**runner, **runs_kit.if_match(edited.json()["thread"])})
    assert withdrawn.status_code == 200 and withdrawn.json()["entry"]["status"] == "withdrawn", withdrawn.text


async def test_malformed_overrides_fail_before_anything_is_stored(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    thread_id = (await runs_kit.start_thread(service, agent, "first"))["thread"]["id"]
    missing = {"overrides": {"model": "missing"}}

    appended = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "queued", options=missing))
    assert appended.status_code == 400, appended.text
    assert len(await runs_kit.inbox(service, thread_id)) == 1

    queued = await _queue(service, runs_kit, thread_id, agent, "queued")
    thread = runs_kit.if_match(await runs_kit.get_thread(service, thread_id))
    edited = await service.client.patch(
        f"{service.api}/threads/{thread_id}/inbox/{queued['id']}", json={"options": missing}, headers=thread
    )
    assert edited.status_code == 400, edited.text
    assert (await runs_kit.inbox(service, thread_id))[1]["options"] == queued["options"]


async def test_the_inbox_holds_a_bounded_count_of_outstanding_input(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    small = settings.model_copy(update={"control": settings.control.model_copy(update={"inbox_count": 2})})
    async with serve(settings=small) as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        thread_id = (await runs_kit.start_thread(service, agent, "assigned"))["thread"]["id"]
        queued = await _queue(service, runs_kit, thread_id, agent, "pending")

        # The assigned source and the pending entry both count.
        full = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "one too many"))
        assert full.status_code == 429, full.text
        details = full.json()["error"]["details"]
        assert (details["count"], details["limit_count"]) == (2, 2)
        thread = runs_kit.if_match(await runs_kit.get_thread(service, thread_id))
        withdrawn = await service.client.delete(
            f"{service.api}/threads/{thread_id}/inbox/{queued['id']}", headers=thread
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert (await runs_kit.submit(service, thread_id, runs_kit.message(agent, "fits again"))).status_code == 201


async def test_pending_entries_are_edited_reordered_and_withdrawn(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    thread_id = (await runs_kit.start_thread(service, agent, "first"))["thread"]["id"]
    a, b, c = [(await _queue(service, runs_kit, thread_id, agent, text))["id"] for text in "abc"]
    inbox = f"{service.api}/threads/{thread_id}/inbox"
    thread = await runs_kit.get_thread(service, thread_id)

    edit = {"payload": {"content": [{"type": "text", "text": "a, edited"}]}, "delivery": "steer"}
    edited = await service.client.patch(f"{inbox}/{a}", json=edit, headers=runs_kit.if_match(thread))
    assert edited.status_code == 200, edited.text
    assert edited.json()["entry"]["delivery"] == "steer"
    stale = await service.client.patch(f"{inbox}/{a}", json=edit, headers=runs_kit.if_match(thread))
    assert stale.status_code == 412, stale.text
    thread = edited.json()["thread"]

    partial = await service.client.put(f"{inbox}/order", json={"entry_ids": [c, a]}, headers=runs_kit.if_match(thread))
    assert partial.status_code == 400, partial.text
    ordered = await service.client.put(
        f"{inbox}/order", json={"entry_ids": [c, a, b]}, headers=runs_kit.if_match(thread)
    )
    assert ordered.status_code == 200, ordered.text
    assert [entry["id"] for entry in await runs_kit.inbox(service, thread_id)][1:] == [c, a, b]

    withdrawn = await service.client.delete(f"{inbox}/{b}", headers=runs_kit.if_match(ordered.json()))
    assert withdrawn.status_code == 200, withdrawn.text
    read = await service.client.get(f"{inbox}/{b}")
    assert read.status_code == 200 and read.json() == withdrawn.json()["entry"], read.text
    assert (await service.client.get(f"{inbox}/{new_object_id('inb')}")).status_code == 404
    thread = withdrawn.json()["thread"]
    again = await service.client.patch(f"{inbox}/{b}", json=edit, headers=runs_kit.if_match(thread))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "entry_withdrawn", again.text


async def test_archive_withdraws_input_and_cancels_the_accepted_run(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "first")
    thread_id, run_id = submitted["thread"]["id"], submitted["run"]["id"]
    await _queue(service, runs_kit, thread_id, agent, "queued")
    archive = f"{service.api}/threads/{thread_id}/archive"
    thread = await runs_kit.get_thread(service, thread_id)

    archived = await service.client.post(archive, headers=runs_kit.if_match(thread))
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None, archived.text
    assert (await runs_kit.get_run(service, run_id))["status"] == "cancelled"
    assert _dispositions(await runs_kit.inbox(service, thread_id)) == [("failed", "run_ended"), ("withdrawn", None)]
    refused = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "too late"))
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "archived", refused.text
    repeated = await service.client.post(archive, headers=runs_kit.if_match(archived.json()))
    assert repeated.status_code == 200 and repeated.json()["archived_at"] == archived.json()["archived_at"]


async def test_archive_withdraws_the_steers_a_completing_run_left_unused(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """The run completes before its worker notices the archive; its unused steers join the withdrawn input."""
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("Done", gate=gate)
    submitted = await runs_kit.start_thread(service, agent, "first")
    thread_id, run_id = submitted["thread"]["id"], submitted["run"]["id"]
    # The attempt's own boundaries take 1 KiB batches, so they never take the steer below themselves.
    settings = service.runtime.settings
    worker = settings.worker.model_copy(update={"delivery_bytes": 1024})
    running = await runs_kit.attempt(
        service, runtime=replace(service.runtime, settings=settings.model_copy(update={"worker": worker}))
    )
    await scripted_model.request()
    # Request arrival does not await the checkpoint whose input consumption changes the thread's ETag.
    await runs_kit.checkpointed(service, run_id)
    steer = (await runs_kit.submit(service, thread_id, runs_kit.message(agent, "x" * 2048))).json()["entry"]
    # Assigned at a safe boundary as a worker assigns it, but never offered to the run's last request.
    async with transaction(service.runtime.storage) as session:
        thread_row, run_row = await lock(session, ThreadRow, thread_id), await lock(session, RunRow, run_id)
        assert thread_row is not None and run_row is not None
        assigned = await inbox.assign_steers(
            session,
            thread_row,
            run_row,
            max_count=settings.worker.delivery_count,
            max_bytes=settings.worker.delivery_bytes,
            scan=settings.control.inbox_count,
        )
    assert [entry.id for entry in assigned] == [steer["id"]]
    thread = await runs_kit.get_thread(service, thread_id)
    archived = await service.client.post(
        f"{service.api}/threads/{thread_id}/archive", headers=runs_kit.if_match(thread)
    )
    assert archived.status_code == 200, archived.text

    gate.set()
    await running
    assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
    assert entries[steer["id"]]["status"] == "withdrawn", entries[steer["id"]]
