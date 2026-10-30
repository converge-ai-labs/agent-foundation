"""Deployment bounds govern every takeover and renewal without fixed request-schema ceilings."""

from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.runs.browsers import admission
from a13n_service.settings import Control

from .test_browsers import bound_run, command, setup

pytestmark = pytest.mark.anyio


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={
            "control": Control(
                browser_control_min_seconds=10,
                browser_control_max_seconds=7200,
                browser_control_seconds=120,
            )
        }
    )


@pytest.mark.parametrize("requested", [10, 7200, None])
async def test_configured_takeover_and_renew_bounds_and_default(service, runs_kit, scripted_model, requested):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    # Keep takeover draining so a short allowed control duration need not satisfy supplier maintenance.
    await admission.admit(service.runtime, lease, binding)
    body = {} if requested is None else {"lease_seconds": requested}
    before = datetime.now(UTC)
    taken = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id, **body)
    after = datetime.now(UTC)
    duration = 120 if requested is None else requested
    deadline = datetime.fromisoformat(taken["control_expires_at"])
    assert before + timedelta(seconds=duration) <= deadline <= after + timedelta(seconds=duration)
    assert taken["control"] == "draining"

    path = f"{service.api}/browsers/{browser['id']}/renew"
    identity = {"generation": taken["generation"], "episode": taken["episode"]}
    # Exercise both configurable endpoints and omission on every episode, preserving monotonic max.
    for seconds in (10, None, 7200):
        body = {} if seconds is None else {"lease_seconds": seconds}
        before = datetime.now(UTC)
        response = await service.client.post(path, json={**identity, **body})
        after = datetime.now(UTC)
        assert response.status_code == 200, response.text
        updated = datetime.fromisoformat(response.json()["control_expires_at"])
        duration = 120 if seconds is None else seconds
        assert max(deadline, before + timedelta(seconds=duration)) <= updated
        assert updated <= max(deadline, after + timedelta(seconds=duration))
        deadline = updated


async def test_invalid_takeover_and_renew_leave_control_unchanged(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await admission.admit(service.runtime, lease, binding)
    path = f"{service.api}/browsers/{browser['id']}"
    invalid = (9, 7201, 0, -1, 10.0, True, "120")
    for operation in ("takeover", "renew"):
        if operation == "renew":
            await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        current = (await service.client.get(path)).json()
        identity = (
            {"thread_id": lease.thread_id}
            if operation == "takeover"
            else {"generation": current["generation"], "episode": current["episode"]}
        )
        for seconds in invalid:
            response = await service.client.post(
                f"{path}/{operation}",
                json={**identity, "lease_seconds": seconds},
                headers=runs_kit.if_match(current),
            )
            assert response.status_code == 400, response.text
            assert (await service.client.get(path)).json() == current
