"""Real database checks of batching, live revocation and per-caller isolation."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.infra.db import transaction
from a13n_service.runs.claim import claim
from a13n_service.runs.environments.clients.authority import Check, resolve, resolve_many
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.service import Attachment
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.tool_executions.routing import Caller
from sqlalchemy import event

from .test_client_environments import register

pytestmark = pytest.mark.anyio


@contextmanager
def count_sql(runtime):
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(runtime.storage.engine.sync_engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(runtime.storage.engine.sync_engine, "before_cursor_execute", count)


@pytest.fixture
async def checks(service, runs_kit, scripted_model):
    await runs_kit.pause_sweeps(service)
    created = (await register(service)).json()
    identity = created["client"]["environment"]["id"]
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(2):
        await runs_kit.start_thread(
            service, agent, "Test authorization", environments=[{"name": "pc", "environment_id": identity}]
        )
    leases = await claim(service.runtime, worker_id="authority-test", worker_build="test", limit=2)
    assert len(leases) == 2
    return [
        Check(
            Request(
                environment_id=identity,
                epoch="a" * 32,
                scope_id=uuid4().hex,
                thread_id=lease.thread_id,
                caller=Caller(run_id=lease.run_id, attempt_id=lease.attempt_id, worker_id=lease.worker_id),
                method="environment.describe",
                params={},
                expires_at=datetime.now(UTC) + timedelta(seconds=30),
            ),
            Attachment(identity, "laptop", 1),
            frozenset(),
        )
        for lease in leases
    ]


async def test_batch_authority_uses_set_reads_and_does_not_cache_revocation(service, checks):
    runtime = service.runtime
    batch = checks * 4
    with count_sql(runtime) as separate:
        for check in batch:
            await resolve(runtime, check.request, check.attachment, check.actions)
    with count_sql(runtime) as combined:
        results = await resolve_many(runtime, batch)
    assert all(not isinstance(result, EnvironmentError) for result in results)
    assert len(combined) * len(batch) == len(separate)
    print(f"Client authority SQL: {len(separate)} separate -> {len(combined)} batched ({len(batch)} checks, two runs)")
    async with transaction(runtime.storage) as session:
        config = await session.get_one(ClientSettingsRow, checks[0].request.environment_id)
        config.credential_version += 1
    assert all(isinstance(result, EnvironmentError) for result in await resolve_many(runtime, batch))


@pytest.mark.parametrize("refusal", ["cancelled", "expired", "worker", "thread", "permissions"])
async def test_refused_caller_does_not_revoke_its_sibling(service, checks, refusal):
    first, second = checks
    request = first.request
    if refusal in {"cancelled", "expired"}:
        async with transaction(service.runtime.storage) as session:
            if refusal == "cancelled":
                run = await session.get_one(RunRow, request.caller.run_id)
                run.cancel_requested_at = datetime.now(UTC)
            else:
                attempt = await session.get_one(AttemptRow, request.caller.attempt_id)
                attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif refusal == "worker":
        request = request.model_copy(update={"caller": request.caller.model_copy(update={"worker_id": "wrong"})})
    elif refusal == "thread":
        request = request.model_copy(update={"thread_id": second.request.thread_id})
    first = Check(
        request,
        first.attachment,
        frozenset({"environment.computer.observe"}) if refusal == "permissions" else first.actions,
    )
    rejected, allowed = await resolve_many(service.runtime, [first, second])
    assert isinstance(rejected, EnvironmentError) and rejected.code == "environment_denied"
    assert not isinstance(allowed, EnvironmentError)
