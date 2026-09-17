from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from a13n_service.environments.websocket.authority import ConnectionIdentity, UseIdentity
from a13n_service.environments.websocket.coordination import (
    ConnectionCoordination,
    CoordinationError,
    CoordinationLimits,
    environment_key,
    ticket_key,
)
from redis.asyncio import Redis
from redis.crc import key_slot

pytestmark = pytest.mark.anyio


async def test_coordination_wait_is_bounded_by_lease_horizon(coordination, monkeypatch):
    cancelled = asyncio.Event()

    async def stalled(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(coordination, "_script", stalled)
    async with asyncio.timeout(1):
        with pytest.raises(CoordinationError) as error:
            await coordination.issue("org", "env")
    assert error.value.code == "coordination_unavailable"
    assert cancelled.is_set()


LIMITS = CoordinationLimits(
    lease_ms=150, ticket_ms=1_000, candidate_ms=1_000, retention_ms=5_000, safety_margin_seconds=0.005
)


@pytest.fixture
def coordination(redis_client: Redis) -> ConnectionCoordination:
    return ConnectionCoordination(redis_client, limits=LIMITS)


async def candidate(coordination: ConnectionCoordination, owner: str = "control_first") -> ConnectionIdentity:
    ticket = await coordination.issue("org_test", "env_test")
    admitted = await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id=owner)
    assert admitted.value.status == "connecting"
    identity = admitted.value.connection
    assert identity is not None
    assert identity.connection_id == ticket.connection_id
    return identity


async def online(coordination: ConnectionCoordination) -> ConnectionIdentity:
    identity = await candidate(coordination)
    # A new or lost coordination key cannot prove the absence of old cached leases.
    await asyncio.sleep(LIMITS.lease_ms / 1000 + 0.02)
    await coordination.promote(identity)
    await coordination.online(identity)
    return identity


async def test_tickets_are_bound_single_use_and_stored_only_as_verifiers(
    coordination: ConnectionCoordination, redis_client: Redis
) -> None:
    ticket = await coordination.issue("org_test", "env_test")
    assert ticket.secret not in repr(ticket)
    keys = await redis_client.keys("a13n:*")
    assert all(ticket.secret.encode() not in key for key in keys)
    for key in keys:
        value = await redis_client.get(key)
        assert value is not None and ticket.secret.encode() not in value
    with pytest.raises(CoordinationError, match="coordination") as invalid:
        await coordination.admit("org_other", "env_test", ticket=ticket.secret, owner_instance_id="control")
    assert invalid.value.code in {"ticket_invalid", "authority_lost"}
    assert await redis_client.get(ticket_key(ticket.secret)) is not None
    first = await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="control")
    with pytest.raises(CoordinationError) as replay:
        await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="control")
    assert replay.value.code == "ticket_invalid"
    assert (await coordination.observe("org_test", "env_test")).value.connection == first.value.connection


async def test_concurrent_candidates_do_not_displace_or_extend_the_winner(
    coordination: ConnectionCoordination,
) -> None:
    first = await candidate(coordination)
    before = await coordination.observe("org_test", "env_test")
    ticket = await coordination.issue("org_test", "env_test")
    with pytest.raises(CoordinationError) as busy:
        await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="other")
    assert busy.value.code == "candidate_busy"
    after = await coordination.observe("org_test", "env_test")
    assert after.value.connection == first
    assert after.value.expires_at_ms == before.value.expires_at_ms
    with pytest.raises(CoordinationError) as consumed:
        await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="other")
    assert consumed.value.code == "ticket_invalid"


async def test_takeover_freezes_grants_then_promotes_only_after_exact_fenced_ack(
    coordination: ConnectionCoordination,
) -> None:
    first = await online(coordination)
    active = await coordination.observe("org_test", "env_test")
    use = UseIdentity(first, "eu_first", "run_first", "attempt_first", 1, "worker_first")
    grant = await coordination.acquire_use(use, attempt_expires_at_ms=active.value.now_ms + 5_000)
    second = await candidate(coordination, "control_second")
    waiting = await coordination.observe("org_test", "env_test")
    assert waiting.value.status == "connecting"
    assert waiting.value.connection == second
    assert waiting.value.retiring is not None
    assert waiting.value.retiring.until_ms >= grant.value.expires_at_ms
    for operation in (
        coordination.renew(first),
        coordination.renew_use(use, attempt_expires_at_ms=active.value.now_ms + 5_000),
        coordination.acquire_use(replace(use, use_id="eu_other"), attempt_expires_at_ms=active.value.now_ms + 5_000),
    ):
        with pytest.raises(CoordinationError) as rejected:
            await operation
        assert rejected.value.code == "authority_lost"
    with pytest.raises(CoordinationError) as pending:
        await coordination.promote(second)
    assert pending.value.code == "handover_pending"
    with pytest.raises(CoordinationError):
        await coordination.acknowledge(replace(first, owner_instance_id="forged_owner"))
    await coordination.acknowledge(first)
    initialized = await coordination.promote(second)
    assert initialized.value.status == "connecting"
    assert initialized.value.use is None
    ready = await coordination.online(second)
    assert ready.value.status == "online"
    assert ready.value.connection == second
    with pytest.raises(CoordinationError):
        await coordination.retire(first)
    assert (await coordination.observe("org_test", "env_test")).value.status == "online"


async def test_candidate_failure_keeps_retirement_barrier_and_never_restores_old_owner(
    coordination: ConnectionCoordination,
) -> None:
    old = await online(coordination)
    second = await candidate(coordination, "control_second")
    barrier = (await coordination.observe("org_test", "env_test")).value.barrier_ms
    await coordination.abandon(second)
    failed = await coordination.observe("org_test", "env_test")
    assert failed.value.status == "offline"
    assert failed.value.connection is None
    assert failed.value.barrier_ms == barrier
    with pytest.raises(CoordinationError):
        await coordination.renew(old)
    third = await candidate(coordination, "control_third")
    with pytest.raises(CoordinationError) as pending:
        await coordination.promote(third)
    assert pending.value.code == "handover_pending"
    await coordination.acknowledge(old)
    await coordination.promote(third)


async def test_dead_owner_can_be_replaced_after_the_complete_grant_horizon(
    coordination: ConnectionCoordination,
) -> None:
    old = await online(coordination)
    second = await candidate(coordination, "control_second")
    await asyncio.sleep(LIMITS.lease_ms / 1000 + 0.02)
    await coordination.promote(second)
    await coordination.online(second)
    with pytest.raises(CoordinationError):
        await coordination.renew(old)


async def test_exclusive_use_is_bounded_by_attempt_authority_and_cannot_migrate(
    coordination: ConnectionCoordination,
) -> None:
    connection = await online(coordination)
    observed = await coordination.observe("org_test", "env_test")
    use = UseIdentity(connection, "eu_first", "run_first", "attempt_first", 1, "worker_first")
    attempt_expiry = observed.value.now_ms + 75
    grant = await coordination.acquire_use(use, attempt_expires_at_ms=attempt_expiry)
    assert grant.value.use is not None and grant.value.use.expires_at_ms <= attempt_expiry
    with pytest.raises(CoordinationError) as busy:
        await coordination.acquire_use(replace(use, run_id="run_other"), attempt_expires_at_ms=attempt_expiry)
    assert busy.value.code == "environment_busy"
    with pytest.raises(CoordinationError):
        await coordination.renew_use(replace(use, attempt_fence=2), attempt_expires_at_ms=attempt_expiry)
    await asyncio.sleep(0.09)
    await coordination.renew(connection)
    with pytest.raises(CoordinationError) as expired:
        await coordination.renew_use(use, attempt_expires_at_ms=attempt_expiry + 1_000)
    assert expired.value.code == "authority_lost"
    with pytest.raises(CoordinationError):
        await coordination.acquire_use(replace(use, use_id="eu_second"), attempt_expires_at_ms=attempt_expiry + 1_000)


async def test_lost_redis_history_never_recreates_an_old_lease_or_bypasses_quarantine(
    coordination: ConnectionCoordination, redis_client: Redis
) -> None:
    old = await online(coordination)
    await redis_client.delete(environment_key("org_test", "env_test"))
    with pytest.raises(CoordinationError):
        await coordination.renew(old)
    assert (await coordination.observe("org_test", "env_test")).value.status == "offline"
    new = await candidate(coordination, "control_new")
    with pytest.raises(CoordinationError) as pending:
        await coordination.promote(new)
    assert pending.value.code == "handover_pending"
    with pytest.raises(CoordinationError):
        await coordination.acknowledge(old)
    await asyncio.sleep(LIMITS.lease_ms / 1000 + 0.02)
    await coordination.promote(new)


async def test_wrong_key_type_fails_without_ticket_consumption(
    coordination: ConnectionCoordination, redis_client: Redis
) -> None:
    ticket = await coordination.issue("org_test", "env_test")
    key = environment_key("org_test", "env_test")
    await redis_client.delete(key)
    await redis_client.hset(key, mapping={"invalid": "state"})
    with pytest.raises(CoordinationError) as invalid:
        await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="control")
    assert invalid.value.code == "coordination_unavailable"
    assert await redis_client.get(ticket_key(ticket.secret)) is not None


async def test_relay_keys_share_one_explicit_scripting_slot() -> None:
    assert key_slot(environment_key("org_a", "env_a").encode()) == key_slot(ticket_key("secret").encode())
    assert environment_key("org_a", "env_a") != environment_key("org_b", "env_a")


async def test_competing_admissions_have_exactly_one_winner(coordination: ConnectionCoordination) -> None:
    tickets = await asyncio.gather(*(coordination.issue("org_test", "env_test") for _ in range(4)))
    results = await asyncio.gather(
        *(
            coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id=f"control_{i}")
            for i, ticket in enumerate(tickets)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, CoordinationError) and result.code == "candidate_busy" for result in results) == 3


async def test_ticket_and_candidate_expiry_cannot_be_renewed(redis_client: Redis) -> None:
    coordination = ConnectionCoordination(redis_client, limits=replace(LIMITS, ticket_ms=30, candidate_ms=30))
    ticket = await coordination.issue("org_test", "env_test")
    await asyncio.sleep(0.05)
    with pytest.raises(CoordinationError) as expired:
        await coordination.admit("org_test", "env_test", ticket=ticket.secret, owner_instance_id="control")
    assert expired.value.code == "ticket_invalid"
    admitted = await candidate(coordination)
    await asyncio.sleep(0.05)
    with pytest.raises(CoordinationError) as expired_candidate:
        await coordination.promote(admitted)
    assert expired_candidate.value.code == "candidate_expired"
    assert (await coordination.observe("org_test", "env_test")).value.status == "offline"


async def test_server_incarnation_change_invalidates_restored_authority(
    coordination: ConnectionCoordination, redis_client: Redis
) -> None:
    old = await online(coordination)
    key = environment_key("org_test", "env_test")
    raw = await redis_client.get(key)
    assert raw is not None
    state = json.loads(raw)
    state["server_id"] = "prior-redis-primary"
    await redis_client.set(key, json.dumps(state))
    assert (await coordination.observe("org_test", "env_test")).value.status == "offline"
    with pytest.raises(CoordinationError):
        await coordination.renew(old)
    new = await candidate(coordination, "control_new")
    with pytest.raises(CoordinationError) as pending:
        await coordination.promote(new)
    assert pending.value.code == "handover_pending"


async def test_use_replay_keeps_deadline_and_bigint_fences_do_not_round(coordination: ConnectionCoordination) -> None:
    connection = await online(coordination)
    observed = await coordination.observe("org_test", "env_test")
    use = UseIdentity(connection, "eu_first", "run_first", "attempt_first", 2**53, "worker_first")
    expiry = observed.value.now_ms + 2_000
    first = await coordination.acquire_use(use, attempt_expires_at_ms=expiry)
    replay = await coordination.acquire_use(use, attempt_expires_at_ms=expiry)
    assert first.value.use == replay.value.use
    with pytest.raises(CoordinationError):
        await coordination.renew_use(replace(use, attempt_fence=2**53 + 1), attempt_expires_at_ms=expiry)
