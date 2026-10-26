"""Opt-in Service takeover against real Modal Envd and desktop grants.

Only the model is scripted. SQL, Service HTTP, execution admission, EIP, Modal
management and RFB are real; the fixture seeds the ready lifecycle receipt.
"""

import asyncio
import json
import os
from dataclasses import replace

import pytest
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_service.infra.db import transaction
from a13n_service.providers.environments.modal.configuration import ModalRecipe
from a13n_service.providers.environments.modal.management import ModalManagement
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import provider_identity
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

from ..test_desktop_assistance import control, post
from ..test_desktop_takeover import open_desktop
from .desktop_access_rfb import RFB

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.timeout(900),
    pytest.mark.skipif(
        os.environ.get("A13N_TEST_MODAL_DESKTOP") != "1", reason="Requires explicit Modal desktop opt-in"
    ),
]


async def test_live_modal_user_takeover_and_handback(service, scripted_model, runs_kit, tmp_path):
    await runs_kit.pause_sweeps(service)
    # The direct attempt helper has no Worker renewal loop. Allow bounded cloud
    # latency without changing production lease or renewal behavior.
    settings = service.runtime.settings
    attempt_runtime = replace(
        service.runtime,
        settings=settings.model_copy(update={"worker": settings.worker.model_copy(update={"lease_seconds": 300})}),
    )
    backend = ModalConnectionConfiguration.model_validate_json(os.environ["A13N_TEST_MODAL_BACKEND_JSON"])
    credential = ModalCredential.model_validate_json(os.environ["A13N_TEST_MODAL_CREDENTIAL_JSON"])
    recipe = ModalRecipe(
        image=os.environ["A13N_TEST_MODAL_IMAGE"],
        desktop=True,
        desktop_assistance=True,
        cpu=2,
        memory=4096,
        request_timeout_seconds=300,
    )
    response = await service.client.post(
        f"{service.api}/environment-providers",
        json={
            "type": "modal",
            "name": "Modal live desktop",
            "config": backend.model_dump(mode="json"),
            "credential": json.loads(os.environ["A13N_TEST_MODAL_CREDENTIAL_JSON"]),
        },
    )
    assert response.status_code == 201
    provider_id = response.json()["id"]
    response = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Modal live desktop",
            "provider_id": provider_id,
            "config": {"recipe": recipe.model_dump(mode="json")},
        },
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(f"{service.api}/environments", json={"template_id": response.json()["id"]})
    assert response.status_code == 201, response.text
    environment_id = response.json()["id"]
    manager = ModalManagement(recipe, environment_id, None, NativeRuntime(backend, credential), "op-live")
    running = None
    controller = None
    gate = asyncio.Event()
    try:
        await manager.create()
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(EnvironmentRow, environment_id)
            row.provider_identity = provider_identity(
                service.runtime.registry, await read_provider(session, EnvironmentProviderRow, provider_id)
            )
            row.status = "ready"
            row.operation_id = row.operation_started_at = None
            row.handle = Handle(recipe=recipe.model_dump(mode="json"), state=manager.state).model_dump(mode="json")
        agent = await runs_kit.create_agent(service, scripted_model, toolsets={"computer": {"enabled": True}})
        scripted_model.call("computer_observe", {}, call_id="observe_before")
        scripted_model.call("computer_observe", {}, call_id="stale", gate=gate)
        first = await runs_kit.start_thread(
            service,
            agent,
            "Inspect the desktop, then wait for user correction",
            environments=[
                {
                    "name": "workspace",
                    "environment_id": environment_id,
                    "permission_ceiling": {
                        "operations": ["environment.computer.observe", "environment.computer.click"]
                    },
                }
            ],
        )
        running = await runs_kit.attempt(service, runtime=attempt_runtime)
        await asyncio.wait_for(scripted_model.request(), 60)
        await asyncio.wait_for(scripted_model.request(), 120)
        items = await runs_kit.items(service, first["run"]["id"])
        observed = next(item for item in items["items"] if item["content"].get("toolCallId") == "observe_before")
        assert observed["content"]["tool_images"]
        view = await open_desktop(service, first["run"])
        assert (await post(service, view, "control", control(view))).status_code == 409
        requested = await post(service, view, "request-control", {})
        assert requested.status_code == 200, requested.text
        assert requested.json()["state"] == "preparing"
        gate.set()
        await asyncio.wait_for(running, 120)
        run = await runs_kit.get_run(service, first["run"]["id"])
        assert run["status"] == "waiting" and run["wait_reason"] == "desktop_control", run.get("failure")
        claimed = await post(service, view, "control", control(view))
        assert claimed.status_code == 200
        access = claimed.json()
        view = access["assistance"]
        controller = await asyncio.to_thread(RFB, access["websocket_url"], access["password"])
        assert (await asyncio.to_thread(controller.frame))["pixel_bytes"] > 0
        await asyncio.to_thread(controller.pointer, 300, 300)
        completed = await post(service, view, "complete", control(view))
        assert completed.status_code == 200, completed.text
        assert await asyncio.to_thread(controller.closed)
        resumed = await service.client.post(
            f"{service.api}/runs/{run['id']}/resume", json={"calls": {}, "approvals": {}}, headers=runs_kit.fresh_key()
        )
        assert resumed.status_code == 201, resumed.text
        scripted_model.call("computer_observe", {}, call_id="observe_after")
        scripted_model.say("The desktop was observed again after the user returned control.")
        running = await runs_kit.attempt(service, runtime=attempt_runtime)
        await asyncio.wait_for(running, 180)
        successor = await runs_kit.get_run(service, resumed.json()["id"])
        assert successor["status"] == "completed", successor.get("failure")
        items = await runs_kit.items(service, successor["id"])
        observed = next(item for item in items["items"] if item["content"].get("toolCallId") == "observe_after")
        assert observed["content"]["tool_images"]
        (tmp_path / "modal-service-takeover.json").write_text(
            json.dumps(
                {
                    "real_envd_observation": True,
                    "safe_pause": True,
                    "real_rfb_control": True,
                    "revocation_disconnect": True,
                    "successor_reobserved_and_completed": True,
                },
                indent=2,
            )
        )
    finally:
        gate.set()
        if running is not None and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        if controller is not None:
            await asyncio.to_thread(controller.close)
        try:
            await manager.destroy()
        finally:
            await manager.close()
