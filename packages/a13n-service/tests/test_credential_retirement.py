"""Only confirmed native loss retires credential references; provider uncertainty retains evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.environments import lifecycle, renewal
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.runs.environments.maintenance import maintain_environments
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.seal import seal
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from sqlalchemy import select

from .environments_support import BACKEND, act, backdate, environment, interrupt, start
from .test_egress_sessions import prepared as prepared
from .test_environments_external import insert as insert_external
from .test_session_credentials import selection

pytestmark = pytest.mark.anyio


async def references(p, environment_id=None):
    async with short_session(p.service.runtime.storage) as db:
        return list(
            await db.scalars(
                select(SessionCredentialRow.env).where(
                    SessionCredentialRow.environment_id == (environment_id or p.mount.environment_id)
                )
            )
        )


async def finish(p):
    async with transaction(p.service.runtime.storage) as db:
        run = await db.get_one(RunRow, p.lease.run_id)
        await seal(
            db,
            p.service.runtime,
            await db.get_one(ThreadRow, run.thread_id),
            run,
            await db.get_one(AttemptRow, p.lease.attempt_id),
            Outcome.cancelled(),
            at=datetime.now(UTC),
        )


async def begin(p, phase):
    async with transaction(p.service.runtime.storage) as db:
        row = await db.get_one(EnvironmentRow, p.mount.environment_id, with_for_update=True)
        await lifecycle.begin(db, row, phase)


async def provision(p):
    await begin(p, "creating")
    await lifecycle.advance(p.service.runtime, p.mount.environment_id, owner="credential-test")
    async with p.borrower.borrow(p.device, **p.options):
        assert await references(p) == ["GH_TOKEN"]
    await finish(p)


async def test_destroy_retains_references_until_result_is_confirmed(prepared):
    p = prepared
    await provision(p)
    other = await start(
        p.service,
        options={
            "environment": {
                "mode": "fresh",
                "template_id": p.service.template["id"],
                "session_configuration": selection(p.credential["id"]),
            }
        },
    )
    assert other["run"] is not None, other
    other_id = other["run"]["environment_mounts"][0]["environment_id"]
    assert (await act(p.service, "DELETE", p.mount.environment_id))[0] == 202
    operation = await lifecycle.claim(p.service.runtime, p.mount.environment_id, owner="cleanup")
    assert operation is not None
    await lifecycle.publish(
        p.service.runtime, replace(operation, generation=operation.generation + 1), lifecycle.Outcome(state=None)
    )
    assert await references(p) == ["GH_TOKEN"], "a superseded result cannot remove current records"
    BACKEND.lose_response = True
    outcome = await lifecycle.perform(p.service.runtime, operation)
    assert outcome.fault is not None and outcome.fault.certainty == "unknown"
    await lifecycle.publish(p.service.runtime, operation, outcome)
    assert await references(p) == ["GH_TOKEN"]
    await lifecycle.advance(p.service.runtime, p.mount.environment_id, owner="cleanup")
    assert (await environment(p.service, p.mount.environment_id))["status"] == "deleted"
    assert await references(p) == []
    assert await references(p, other_id) == ["GH_TOKEN"]
    # Destruction clears the runtime index, not the reusable resource or historical Run selection.
    resource = await p.service.client.get(f"{p.service.api}/egress-credentials/{p.credential['id']}")
    assert resource.json()["state"] == "active"
    async with short_session(p.service.runtime.storage) as db:
        run = await db.get_one(RunRow, p.lease.run_id)
        assert run.environment_mounts[0]["session_configuration"] == p.mount.session_configuration.model_dump(
            mode="json"
        )


async def test_confirmed_loss_during_start_retires_references(prepared):
    p = prepared
    await provision(p)
    await begin(p, "stopping")
    await lifecycle.advance(p.service.runtime, p.mount.environment_id, owner="stop")
    assert await references(p) == ["GH_TOKEN"], "a stop alone does not prove Session destruction"
    del BACKEND.instances[p.mount.environment_id]
    await begin(p, "starting")
    await lifecycle.advance(p.service.runtime, p.mount.environment_id, owner="start")
    assert (await environment(p.service, p.mount.environment_id))["failure"]["code"] == "environment_lost"
    assert await references(p) == []


async def test_renewal_requires_current_claim_and_unchanged_provider_authority(prepared):
    p = prepared
    await provision(p)
    await backdate(p.service, p.mount.environment_id, renew_at=timedelta(seconds=1))
    claimed = await renewal.claim(p.service.runtime, p.mount.environment_id)
    assert claimed is not None
    del BACKEND.instances[p.mount.environment_id]
    outcome = await renewal.perform(p.service.runtime, claimed)
    assert isinstance(outcome, Exception)
    # An answer to an expired claim cannot remove evidence belonging to the current owner.
    await renewal.publish(p.service.runtime, replace(claimed, claim=claimed.claim - timedelta(seconds=1)), outcome)
    assert await references(p) == ["GH_TOKEN"]
    # A different provider account not seeing the sandbox does not establish destruction.
    await renewal.publish(p.service.runtime, replace(claimed, credential_changed=True), outcome)
    assert await references(p) == ["GH_TOKEN"]
    await backdate(p.service, p.mount.environment_id, renew_at=timedelta(seconds=1))
    current = await renewal.claim(p.service.runtime, p.mount.environment_id)
    assert current is not None
    await renewal.publish(p.service.runtime, current, outcome)
    assert await references(p) == []


@pytest.mark.parametrize("automatic", [False, True])
async def test_uncreated_reservation_retirement_discards_unapplied_references(prepared, automatic):
    p = prepared
    await finish(p)
    if automatic:
        path = f"{p.service.api}/environment-templates/{p.service.template['id']}"
        current = await p.service.client.get(path)
        changed = await p.service.client.patch(
            path, headers={"if-match": current.headers["etag"]}, json={"config": {"delete_after_seconds": 3600}}
        )
        assert changed.status_code == 200, changed.text
        await backdate(p.service, p.mount.environment_id, last_used_at=timedelta(days=1))
        await maintain_environments(p.service.runtime, owner="cleanup")
    else:
        assert (await act(p.service, "DELETE", p.mount.environment_id))[0] == 202
    assert (await environment(p.service, p.mount.environment_id))["status"] == "deleted"
    assert await references(p) == []
    assert p.device.opens == p.device.attaches == 0 and BACKEND.preparations == []


async def test_external_deregistration_retains_uncertain_session_references(prepared):
    p = prepared
    external_id = await insert_external(p.service, "https://laptop.test")
    accepted = await start(
        p.service,
        options={
            "environment": {
                "mode": "existing",
                "environment_id": external_id,
                "session_configuration": selection(p.credential["id"]),
            }
        },
    )
    assert accepted["run"] is not None, accepted
    await interrupt(p.service, accepted["run"]["id"])
    assert (await act(p.service, "DELETE", external_id))[0] == 202
    assert await references(p, external_id) == ["GH_TOKEN"]
    result = await p.service.client.get(f"{p.service.api}/environments/{external_id}/credential-sessions")
    assert result.status_code == 200 and result.json()["items"][0]["status"] == "unknown"
