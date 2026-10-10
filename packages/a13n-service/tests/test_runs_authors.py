"""Message attribution follows the entry and is limited to a readable session."""

import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.tables import PrincipalRow
from sqlalchemy import update

pytestmark = pytest.mark.anyio


async def member(service: SimpleNamespace, stack: AsyncExitStack, role: str) -> httpx2.AsyncClient:
    invitation = await service.client.post(
        f"{service.workspace}/invitations", json={"email": f"{role}@example.com", "role": role}
    )
    assert invitation.status_code == 201, invitation.text
    url = invitation.json()["invitation_url"]
    client = await stack.enter_async_context(
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test")
    )
    path = "/api/v1/invitations/" + url.split("/invitations/", 1)[1].split("#", 1)[0]
    accepted = await client.post(
        path,
        json={"token": url.split("#token=", 1)[1], "password": "member-password-1234", "name": "Alex"},
    )
    assert accepted.status_code == 200, accepted.text
    client.headers.update(
        {"x-csrf-token": accepted.json()["csrf_token"], "x-workspace-id": service.tenant.workspace_id}
    )
    return client


async def test_authors_follow_initial_queued_and_steering_entries(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"enabled": True}})
    gate = asyncio.Event()
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", gate=gate)
    scripted_model.say("Done")
    first = await runs_kit.start_thread(service, agent, "initial request")
    path = f"{service.api}/sessions/{first['thread']['session_id']}/message-authors"
    async with AsyncExitStack() as stack:
        sender = await member(service, stack, "runner")
        viewer = await member(service, stack, "viewer")
        sender_id = (await sender.get("/api/v1/users/me")).json()["id"]
        entries = [first["entry"]]
        running = await runs_kit.attempt(service)
        await scripted_model.request()
        for delivery in ("next_run", "steer"):
            result = await sender.post(
                f"{service.api}/threads/{first['thread']['id']}/inbox",
                json=runs_kit.message(agent, delivery, delivery=delivery),
                headers={"idempotency-key": uuid4().hex},
            )
            assert result.status_code == 201, result.text
            entries.append(result.json()["entry"])
        params = [("entry_id", entry["id"]) for entry in entries]
        before = await viewer.get(path, params=params)
        assert before.status_code == 200, before.text
        by_id = {item["entry_id"]: item for item in before.json()["items"]}
        assert by_id[first["entry"]["id"]]["principal_id"] == service.tenant.principal_id
        for entry in entries[1:]:
            author = by_id[entry["id"]]
            assert author["principal_id"] == sender_id
            assert author["principal"]["email"] == "runner@example.com"
            assert author["submitted_at"] == entry["created_at"]
        # This disclosure does not grant access to the administrative member directory.
        assert (await viewer.get(f"{service.workspace}/grants")).status_code == 403
        gate.set()
        await running
        statuses = {entry["id"]: entry for entry in await runs_kit.inbox(service, first["thread"]["id"])}
        assert statuses[entries[2]["id"]]["assigned_run_id"] == first["run"]["id"]
        assert statuses[entries[2]["id"]]["status"] == "consumed"
        queued = await runs_kit.get_run(service, statuses[entries[1]["id"]]["assigned_run_id"])
        assert queued["source_entry_id"] == entries[1]["id"] and queued["principal_id"] == sender_id
        assert (await viewer.get(path, params=params)).json() == before.json()


async def test_author_lookup_is_bounded_and_session_scoped(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "first")
    other = await runs_kit.start_thread(service, agent, "other")
    path = f"{service.api}/sessions/{first['thread']['session_id']}/message-authors"
    ids = [first["entry"]["id"], other["entry"]["id"], new_object_id("inb"), "legacy-source", first["entry"]["id"]]
    result = await service.client.get(path, params=[("entry_id", value) for value in ids])
    assert result.status_code == 200, result.text
    assert [item["entry_id"] for item in result.json()["items"]] == [first["entry"]["id"]]
    for values in ([], [""], ["a" * 73], [first["entry"]["id"]] * 101):
        assert (await service.client.get(path, params=[("entry_id", value) for value in values])).status_code == 400
    other_workspace = (await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})).json()
    params = {"entry_id": first["entry"]["id"]}
    assert (
        await service.client.get(path, params=params, headers={"x-workspace-id": other_workspace["id"]})
    ).status_code == 404
    async with AsyncExitStack() as stack:
        viewer = await member(service, stack, "viewer")
        assert (
            await viewer.get(path, params=params, headers={"x-workspace-id": other_workspace["id"]})
        ).status_code == 403
        anonymous = await stack.enter_async_context(
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test")
        )
        assert (
            await anonymous.get(path, params=params, headers={"x-workspace-id": service.tenant.workspace_id})
        ).status_code == 401


async def test_service_account_author_uses_current_profile_even_when_disabled(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    accounts = f"{service.workspace}/service-accounts"
    account = (await service.client.post(accounts, json={"name": "Automation"})).json()
    key = (await service.client.post(f"{accounts}/{account['id']}/keys", json={"name": "test"})).json()
    receipt = await service.client.post(
        f"{service.api}/threads",
        json=runs_kit.message(agent, "scheduled request"),
        headers={"authorization": "Bearer " + key["secret"], "idempotency-key": uuid4().hex},
    )
    assert receipt.status_code == 201, receipt.text
    entry = receipt.json()["entry"]
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(PrincipalRow)
            .where(PrincipalRow.id == account["id"])
            .values(name="Renamed automation", status="disabled")
        )
    path = f"{service.api}/sessions/{receipt.json()['thread']['session_id']}/message-authors"
    author = (await service.client.get(path, params={"entry_id": entry["id"]})).json()["items"][0]
    assert author["principal_id"] == account["id"]
    assert author["principal"] == {
        "id": account["id"],
        "kind": "service_account",
        "name": "Renamed automation",
        "email": None,
        "status": "disabled",
        "image_url": None,
    }
