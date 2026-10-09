"""Renewal: the ready sandboxes of a type that ends them unless renewed, kept alive by their own sweep."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_service.distribution import OSS
from a13n_service.infra.db import transaction
from a13n_service.runs.environments import lifecycle, renewal
from a13n_service.runs.environments.lifecycle import advance
from a13n_service.runs.environments.maintenance import maintain_environments, renew_environments, renewal_sweep
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import func, select, text, update

from .environments_support import (
    BACKEND,
    act,
    backdate,
    environment,
    follow_up,
    interrupt,
    reason,
    reserve,
    start,
    with_fake_providers,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def env(service) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    return await with_fake_providers(service)


async def expiring_template(env: SimpleNamespace, **provider: object) -> str:
    """A template of the expiring type, on its own provider."""
    created = await env.client.post(
        f"{env.api}/environment-providers",
        json={"type": "expiring", "name": "Expiring", **provider},
    )
    assert created.status_code == 201, created.text
    template = await env.client.post(
        f"{env.api}/environment-templates",
        json={"name": "Expiring", "provider_id": created.json()["id"]},
    )
    assert template.status_code == 201, template.text
    return template.json()["id"]


async def ready_sandbox(env: SimpleNamespace, **provider: object) -> str:
    """A ready sandbox of the expiring type, renewed once, so its expiry is known."""
    environment_id = (await reserve(env, await expiring_template(env, **provider)))["id"]
    await maintain_environments(env.runtime, owner="sweep")
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [environment_id]
    return environment_id


async def schedule(env: SimpleNamespace, environment_id: str) -> tuple[datetime | None, datetime | None]:
    """When the next renewal is due, and the expiry the provider last reported."""
    async with transaction(env.runtime.storage) as session:
        query = select(EnvironmentRow.renew_at, EnvironmentRow.expires_at).where(EnvironmentRow.id == environment_id)
        renew_at, expires_at = (await session.execute(query)).one()
    return renew_at, expires_at


async def due_in(env: SimpleNamespace, environment_id: str) -> timedelta:
    """How long until the next renewal is due."""
    async with transaction(env.runtime.storage) as session:
        query = select(EnvironmentRow.renew_at - func.now()).where(EnvironmentRow.id == environment_id)
        due = await session.scalar(query)
    assert due is not None
    return due


async def due_now(env: SimpleNamespace, environment_id: str) -> None:
    await backdate(env, environment_id, renew_at=timedelta(seconds=1))


async def age_failure(env: SimpleNamespace, environment_id: str, age: timedelta) -> None:
    """Move the recorded failure's first occurrence into the past, as it lasting would."""
    at = text("jsonb_set(failure, '{at}', to_jsonb(now() - make_interval(secs => :age)))")
    at = at.bindparams(age=age.total_seconds())
    async with transaction(env.runtime.storage) as session:
        await session.execute(update(EnvironmentRow).where(EnvironmentRow.id == environment_id).values(failure=at))


async def handle(env: SimpleNamespace, environment_id: str) -> dict:
    async with transaction(env.runtime.storage) as session:
        value = await session.scalar(select(EnvironmentRow.handle).where(EnvironmentRow.id == environment_id))
    assert value is not None
    return value


async def restart(env: SimpleNamespace, environment_id: str) -> None:
    """Start a stopped instance as a waiting attempt would."""
    async with transaction(env.runtime.storage) as session:
        row = await session.get(EnvironmentRow, environment_id, with_for_update=True)
        assert row is not None and row.status == "stopped"
        await lifecycle.begin(session, row, "starting")
    await advance(env.runtime, environment_id, owner="test")
    assert (await environment(env, environment_id))["status"] == "ready"


async def test_renewals_have_their_own_sweep_and_fall_due_halfway_to_the_expiry(env) -> None:  # type: ignore[no-untyped-def]
    assert renewal_sweep in OSS.sweeps
    expiring = (await reserve(env, await expiring_template(env)))["id"]
    plain = (await reserve(env, env.template["id"]))["id"]
    await maintain_environments(env.runtime, owner="sweep")
    for environment_id in (expiring, plain):
        assert (await environment(env, environment_id))["status"] == "ready"
    # Becoming ready makes the first renewal due at once; a type that never ends sandboxes has none.
    assert await schedule(env, plain) == (None, None)
    assert await due_in(env, expiring) <= timedelta() and (await schedule(env, expiring))[1] is None
    await maintain_environments(env.runtime, owner="sweep")
    assert BACKEND.renewals == [], "maintenance never renews"
    path = f"{env.api}/environments/{expiring}"
    etag = (await env.client.get(path)).headers["etag"]

    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring]
    # The next renewal is due halfway to the expiry the provider reports, a keepalive horizon of 300 s away.
    assert (await schedule(env, expiring))[1] == BACKEND.expiries[expiring]
    assert timedelta(seconds=140) < await due_in(env, expiring) <= timedelta(seconds=150)
    assert (await env.client.get(path)).headers["etag"] == etag, "renewal is invisible to clients"
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring], "not due yet"
    await due_now(env, expiring)
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring] * 2

    # Beginning an operation ends renewal: a stopping or stopped sandbox is never renewed.
    code, body = await act(env, "POST", f"{expiring}/stop")
    assert (code, body["status"]) == (202, "stopping") and await schedule(env, expiring) == (None, None)
    await maintain_environments(env.runtime, owner="sweep")
    await renew_environments(env.runtime)
    assert (await environment(env, expiring))["status"] == "stopped"
    assert await schedule(env, expiring) == (None, None) and BACKEND.renewals == [expiring] * 2

    # Becoming ready again renews it again.
    await restart(env, expiring)
    await renew_environments(env.runtime)
    assert BACKEND.renewals == [expiring] * 3


async def test_an_unclassified_renewal_error_is_locatable_without_leaking_its_payload(env, caplog) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    BACKEND.renewal_error = RuntimeError("secret_provider_body")
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    failed = await environment(env, sandbox)
    assert failed["status"] == "ready" and failed["failure"]["code"] == "environment_operation_failed"
    [failure] = [record for record in caplog.records if record.getMessage() == "Environment renewal failed"]
    assert failure.environment_id == sandbox
    assert failure.exception_details[0]["type"] == "builtins.RuntimeError"
    assert failure.exception_details[0]["frames"]
    assert "secret_provider_body" not in json.dumps(vars(failure))
    BACKEND.renewal_error = None
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None


async def test_a_failed_renewal_backs_off_and_one_the_provider_cannot_grant_waits_for_the_expiry(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    path = f"{env.api}/environments/{sandbox}"

    # A transient failure is recorded without refusing use, and retried halfway to the expiry still known.
    BACKEND.renewal_error = provider_error("fake", "provider_unavailable", EnvironmentProviderErrorCategory.UNAVAILABLE)
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    failed = await environment(env, sandbox)
    assert failed["status"] == "ready" and failed["failure"]["code"] == "provider_unavailable"
    assert not failed["failure"]["permanent"] and failed["failure"]["operation_id"] is None
    assert timedelta(seconds=140) < await due_in(env, sandbox) <= timedelta(seconds=150)
    submitted = await start(env, environments=[{"name": "workspace", "environment_id": sandbox}])
    await interrupt(env, submitted["run"]["id"])

    # Past the expiry, the retry waits as long as the same failure has lasted, at most ten minutes; a repeated
    # failure keeps its first record.
    for lasted, wait in ((timedelta(minutes=3), timedelta(minutes=3)), (timedelta(hours=1), timedelta(minutes=10))):
        await age_failure(env, sandbox, lasted)
        await backdate(env, sandbox, renew_at=timedelta(seconds=1), expires_at=timedelta(seconds=1))
        etag = (await env.client.get(path)).headers["etag"]
        await renew_environments(env.runtime)
        assert (await env.client.get(path)).headers["etag"] == etag
        assert abs(await due_in(env, sandbox) - wait) < timedelta(seconds=10)

    # A failure repeating cannot fix keeps its permanence, and refuses new use until a renewal succeeds.
    BACKEND.renewal_error = provider_error("fake", "provider_spec_invalid", EnvironmentProviderErrorCategory.INVALID)
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    refused = await environment(env, sandbox)
    assert (refused["failure"]["code"], refused["failure"]["permanent"]) == ("provider_spec_invalid", True)
    BACKEND.renewal_error = None
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None

    # A renewal the provider cannot grant, near a vendor's hard lifetime, is expected: nothing is recorded, and the
    # next renewal waits for the reported expiry, when the sandbox is found gone or stopped.
    BACKEND.renewal_error = provider_error(
        "fake", "provider_keepalive_limit", EnvironmentProviderErrorCategory.UNSUPPORTED
    )
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None
    assert await schedule(env, sandbox) == (BACKEND.expiries[sandbox], BACKEND.expiries[sandbox])


async def test_a_stopped_sandbox_waits_for_a_run_and_a_lost_one_for_its_deletion(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env)
    submitted = await start(env, environments=[{"name": "workspace", "environment_id": sandbox}])
    thread_id = submitted["thread"]["id"]
    await interrupt(env, submitted["run"]["id"])

    # A provider that stopped the sandbox itself, at its own time limit say, leaves it stopped for a run to start.
    BACKEND.instances[sandbox] = "stopped"
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    stopped = await environment(env, sandbox)
    assert (stopped["status"], stopped["failure"]) == ("stopped", None)
    assert await schedule(env, sandbox) == (None, None)
    await restart(env, sandbox)

    # A provider that no longer has it makes it lost for good: new use and stopping are refused with that reason.
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    lost = await environment(env, sandbox)
    assert lost["status"] == "ready" and await schedule(env, sandbox) == (None, None)
    failure = lost["failure"]
    assert (failure["code"], failure["permanent"], failure["certainty"]) == ("environment_lost", True, "known")
    refused = (await follow_up(env, thread_id, "refused")).json()
    assert refused["run"] is None and refused["entry"]["failure"]["code"] == "environment_lost", refused
    code, body = await act(env, "POST", f"{sandbox}/stop")
    assert (code, reason(body)) == (409, "environment_lost")
    # Idle, it is not stopped either, which would clear the failure that refuses its use.
    await backdate(env, sandbox, last_used_at=timedelta(hours=2))
    await maintain_environments(env.runtime, owner="sweep")
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"

    # Deleting it also removes it from the thread, whose next run reserves a new primary sandbox.
    code, body = await act(env, "DELETE", sandbox)
    assert (code, body["status"]) == (202, "deleting")
    await advance(env.runtime, sandbox, owner="test")
    assert (await environment(env, sandbox))["status"] == "deleted"
    replaced = await follow_up(env, thread_id, "replaced")
    assert replaced.status_code == 201, replaced.text
    [mount] = replaced.json()["run"]["environment_mounts"]
    assert mount["name"] == "workspace" and mount["environment_id"] != sandbox


async def test_a_sandbox_a_new_credential_cannot_see_is_not_lost(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = await ready_sandbox(env, credential={"api_key": "first"})
    first = (await handle(env, sandbox))["credential_version"]
    assert first is not None
    provider_id = (await environment(env, sandbox))["provider_id"]
    item = f"{env.api}/environment-providers/{provider_id}"

    async def replace_credential(api_key: str) -> None:
        etag = (await env.client.get(item)).headers["etag"]
        body = {"credential": {"api_key": api_key}}
        assert (await env.client.patch(item, json=body, headers={"if-match": etag})).status_code == 200

    # A credential of another account does not see the sandbox, which may well still exist in the first one.
    await replace_credential("other-account")
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    unseen = await environment(env, sandbox)
    assert (unseen["failure"]["code"], unseen["failure"]["permanent"]) == ("provider_credential_changed", False)
    assert (await schedule(env, sandbox))[0] is not None, "renewal goes on"

    # A credential of the first account, rotated, sees it again; renewing records the credential that did.
    await replace_credential("first-rotated")
    BACKEND.instances[sandbox] = "running"
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"] is None
    rotated = (await handle(env, sandbox))["credential_version"]
    assert rotated not in {None, first}

    # The credential that last reached it no longer finding it means it is lost.
    del BACKEND.instances[sandbox]
    await due_now(env, sandbox)
    await renew_environments(env.runtime)
    assert (await environment(env, sandbox))["failure"]["code"] == "environment_lost"


async def test_a_superseded_renewal_publishes_nothing(env) -> None:  # type: ignore[no-untyped-def]
    sandbox = (await reserve(env, await expiring_template(env)))["id"]
    await maintain_environments(env.runtime, owner="sweep")

    # A renewal holds its claim until its deadline; one whose dispatcher died is taken over once it expires.
    crashed = await renewal.claim(env.runtime, sandbox)
    assert crashed is not None and await renewal.claim(env.runtime, sandbox) is None
    await due_now(env, sandbox)
    await renewal.renew(env.runtime, sandbox)
    assert len(BACKEND.renewals) == 1
    expiry = await renewal.perform(env.runtime, crashed)
    assert isinstance(expiry, datetime)
    scheduled = await schedule(env, sandbox)
    await renewal.publish(env.runtime, crashed, expiry)
    assert await schedule(env, sandbox) == scheduled

    # An operation that begins meanwhile supersedes a renewal in flight, whatever it observed.
    await due_now(env, sandbox)
    claimed = await renewal.claim(env.runtime, sandbox)
    assert claimed is not None
    code, body = await act(env, "POST", f"{sandbox}/stop")
    assert (code, body["status"]) == (202, "stopping")
    del BACKEND.instances[sandbox]
    await renewal.publish(env.runtime, claimed, await renewal.perform(env.runtime, claimed))
    stopping = await environment(env, sandbox)
    assert (stopping["status"], stopping["failure"]) == ("stopping", None)
