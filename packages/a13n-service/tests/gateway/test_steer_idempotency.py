"""Steer replay, concurrency, expiry, and request-scoped authentication."""

from dataclasses import replace
from datetime import timedelta

import anyio
import pytest
from a13n_service.iam.auth.passwords import csrf_token, token_hash
from a13n_service.iam.configuration import IdentityConfiguration
from a13n_service.iam.http.authentication import DatabaseAuthenticator
from a13n_service.iam.models import AuthSessionRecord, UserRecord
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError
from a13n_service.memory.behaviors import RunMemorySelectionRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.temporal import utc_now
from sqlalchemy import delete, func, select, text, update
from starlette.requests import HTTPConnection

from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation, _request
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, USER_ID, WORKSPACE_ID
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


@pytest.fixture
async def steer_case(lifecycle_interaction_sessions, tmp_path):
    sessions = lifecycle_interaction_sessions
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    await seed_hook_actor_access(sessions)
    run = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
    )
    return sessions, objects, commands, run


@pytest.mark.parametrize("different_input", [False, True])
async def test_concurrent_same_key_never_allocates_twice(steer_case, monkeypatch, different_input):
    sessions, _, commands, run = steer_case
    ready = anyio.Event()
    entered = 0
    original = commands.active._states.read_run

    async def synchronized_read(source):
        nonlocal entered
        state = await original(source)
        entered += 1
        if entered == 2:
            ready.set()
        await ready.wait()
        return state

    monkeypatch.setattr(commands.active._states, "read_run", synchronized_read)
    results = []
    errors = []

    async def submit(message):
        try:
            results.append(
                await commands.active.steer(
                    actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request(message).input
                )
            )
        except InteractionCommandError as error:
            errors.append(error.code)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(submit, "one")
            tasks.start_soon(submit, "two" if different_input else "one")
    assert errors == []
    assert len({receipt.steer_id for receipt in results}) == 1
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ThreadInboxRecord)) == 1
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread.pending_count == 1
        assert thread.next_delivery_sequence == 2
        row = await database.scalar(select(ThreadInboxRecord))
        assert row.idempotency_actor_id == USER_ID


@pytest.mark.parametrize("preparation_fails", [False, True])
async def test_final_replay_precedes_terminal_target_check(steer_case, monkeypatch, preparation_fails):
    sessions, objects, commands, run = steer_case
    original = commands.active._states.read_run
    first_receipt = None

    async def prepare_after_competing_acceptance(source):
        nonlocal first_receipt
        state = await original(source)
        monkeypatch.setattr(commands.active._states, "read_run", original)
        first_receipt = await commands.active.steer(
            actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input
        )
        await _complete_run(sessions, objects, run_id=run.run_id)
        if preparation_fails:
            raise RunObjectIntegrityError("state changed during preparation")
        return state

    monkeypatch.setattr(commands.active._states, "read_run", prepare_after_competing_acceptance)
    replay = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input
    )
    assert replay == first_receipt

    # Early replay also survives a terminal target and does not need the payload.
    async def unavailable(_source):
        raise AssertionError("replay must not read Run state")

    monkeypatch.setattr(commands.active._states, "read_run", unavailable)
    assert (
        await commands.active.steer(actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input)
        == replay
    )


async def test_key_remains_on_the_original_input_after_twenty_four_hours(steer_case, monkeypatch):
    sessions, _, commands, run = steer_case
    now = NOW
    monkeypatch.setattr(commands.active, "_clock", lambda: now)
    monkeypatch.setattr(commands.active._inbox, "_clock", lambda: now)
    first = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request("one").input
    )
    now += timedelta(hours=24) - timedelta(microseconds=1)
    assert (
        await commands.active.steer(
            actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request("one").input
        )
        == first
    )
    now += timedelta(microseconds=1)
    second = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request("two").input
    )
    assert second == first
    async with transaction(sessions) as database:
        first_row = await database.get(ThreadInboxRecord, first.steer_id)
        assert first_row.idempotency_key_digest is not None
        assert first_row.status == "pending"
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread.pending_count == 1
        assert await database.scalar(select(func.count()).select_from(ThreadInboxRecord)) == 1


async def test_rollback_leaves_no_inbox_or_reserved_key(steer_case):
    sessions, _, commands, run = steer_case

    async def fail(_database, _receipt):
        raise RuntimeError("abort admission")

    with pytest.raises(RuntimeError, match="abort admission"):
        await commands.active.steer(
            actor=_actor(),
            run_id=run.run_id,
            idempotency_key="same",
            input=_request().input,
            transaction_hook=fail,
        )
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ThreadInboxRecord)) == 0
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread.pending_count == 0
        assert thread.next_delivery_sequence == 1
    receipt = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input
    )
    assert receipt.delivery_sequence == 1


async def test_steer_and_replay_do_not_validate_memory_binding(steer_case):
    sessions, _, commands, run = steer_case
    async with transaction(sessions) as database:
        await database.execute(delete(RunMemorySelectionRecord).where(RunMemorySelectionRecord.run_id == run.run_id))
    receipt = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input
    )
    assert (
        await commands.active.steer(actor=_actor(), run_id=run.run_id, idempotency_key="same", input=_request().input)
        == receipt
    )


async def _authenticate(sessions):
    token = "x" * 40
    now = utc_now()
    async with transaction(sessions) as database:
        database.add(
            AuthSessionRecord(
                id="ses_steer_test",
                user_id=USER_ID,
                token_hash=token_hash(token),
                created_at=now,
                expires_at=now + timedelta(days=1),
            )
        )
    configuration = IdentityConfiguration(public_origin="https://example.com")
    request = HTTPConnection(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "path_params": {},
            "headers": [
                (b"cookie", f"a13n_session={token}".encode()),
                (b"origin", b"https://example.com"),
                (b"x-a13n-csrf-token", csrf_token(token).encode()),
                (b"x-a13n-workspace-id", WORKSPACE_ID.encode()),
            ],
        }
    )
    return DatabaseAuthenticator(sessions, configuration), request


async def test_session_steer_reuses_precheck_source_in_fourteen_statements(steer_case):
    sessions, _, commands, run = steer_case
    authenticate, request = await _authenticate(sessions)
    with capture_sql(sessions) as statements:
        actor = await authenticate(request)
        receipt = await commands.active.steer(
            actor=actor, run_id=run.run_id, idempotency_key="same", input=_request().input
        )
    assert len(statements) == 14, statements
    assert not any(
        "pg_advisory" in sql or "run_memory_selections" in sql or "idempotency_evidence" in sql for sql in statements
    )
    assert sum("FROM auth_sessions" in sql for sql in statements) == 1
    assert sum("FROM users" in sql for sql in statements) == 1
    with capture_sql(sessions) as statements:
        actor = await authenticate(request)
        assert (
            await commands.active.steer(actor=actor, run_id=run.run_id, idempotency_key="same", input=_request().input)
            == receipt
        )
    assert len(statements) == 8, statements


async def test_request_identity_is_reused_only_when_explicitly_verified(steer_case):
    sessions, _, commands, run = steer_case
    authenticate, request = await _authenticate(sessions)
    actor = await authenticate(request)
    async with transaction(sessions) as database:
        await database.execute(update(AuthSessionRecord).values(revoked_at=utc_now()))
        await database.execute(update(UserRecord).where(UserRecord.id == USER_ID).values(status="disabled"))
    await commands.active.steer(actor=actor, run_id=run.run_id, idempotency_key="verified", input=_request().input)
    with pytest.raises(InteractionCommandError):
        await commands.active.steer(
            actor=replace(actor, request_authenticated=False),
            run_id=run.run_id,
            idempotency_key="unverified",
            input=_request().input,
        )


async def test_schema_has_keys_without_fingerprint_or_expiry(steer_case):
    sessions, _, commands, run = steer_case
    first = await commands.active.steer(
        actor=_actor(), run_id=run.run_id, idempotency_key="stored", input=_request().input
    )
    async with short_session(sessions) as database:
        columns = set(
            await database.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'thread_inbox'")
            )
        )
        assert "idempotency_key_digest" in columns
        assert {"idempotency_request_digest", "idempotency_expires_at"}.isdisjoint(columns)
        row = await database.get(ThreadInboxRecord, first.steer_id)
        assert row.idempotency_key_digest is not None
