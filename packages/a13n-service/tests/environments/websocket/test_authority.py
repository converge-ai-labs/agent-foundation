from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from a13n_service.environments.websocket.authority import (
    ConnectionIdentity,
    DispatchAuthority,
    DispatchDenied,
    LeaseDeadline,
    UseIdentity,
)

pytestmark = pytest.mark.anyio

CONNECTION = ConnectionIdentity("org_test", "env_test", "ec_test", "epoch_test", "control_test")
USE = UseIdentity(
    CONNECTION, "eu_test", "run_test", "attempt_test", 1, "worker_test", "workspace", admission_deadline_ms=1
)


@pytest.mark.parametrize(
    "foreign",
    [
        replace(USE, connection=replace(CONNECTION, connection_epoch="epoch_other")),
        replace(USE, connection=replace(CONNECTION, owner_instance_id="control_other")),
        replace(USE, connection=replace(CONNECTION, environment_id="env_other")),
        replace(USE, connection=replace(CONNECTION, organization_id="org_other")),
        replace(USE, use_id="eu_other"),
        replace(USE, run_id="run_other"),
        replace(USE, attempt_id="attempt_other"),
        replace(USE, attempt_fence=2),
        replace(USE, worker_instance_id="worker_other"),
        CONNECTION,
    ],
)
async def test_foreign_scope_cannot_dispatch_or_renew(foreign: ConnectionIdentity | UseIdentity) -> None:
    authority = DispatchAuthority(USE, LeaseDeadline(20), clock=lambda: 10)
    with pytest.raises(DispatchDenied):
        async with authority.write(foreign):
            pytest.fail("Foreign scope reached the transport")
    with pytest.raises(DispatchDenied):
        authority.renew(foreign, LeaseDeadline(30))
    authority.check(USE)


async def test_delayed_reply_does_not_extend_from_receipt_time() -> None:
    deadline = LeaseDeadline.confirmed(
        request_started_at=10, server_now_ms=500_000, expires_at_ms=505_000, safety_margin_seconds=0.1
    )
    assert deadline.monotonic_at == 14.9
    authority = DispatchAuthority(USE, deadline, clock=lambda: 15)
    with pytest.raises(DispatchDenied):
        authority.check(USE)
    with pytest.raises(DispatchDenied):
        authority.renew(USE, LeaseDeadline(30))


async def test_expiry_is_irreversible_even_if_clock_moves_back() -> None:
    now = 20.0
    authority = DispatchAuthority(USE, LeaseDeadline(20), clock=lambda: now)
    with pytest.raises(DispatchDenied):
        authority.check(USE)
    now = 10
    with pytest.raises(DispatchDenied):
        authority.renew(USE, LeaseDeadline(40))


async def test_old_renewal_reply_does_not_shorten_confirmed_authority() -> None:
    authority = DispatchAuthority(USE, LeaseDeadline(20), clock=lambda: 10)
    authority.renew(USE, LeaseDeadline(30))
    authority.renew(USE, LeaseDeadline(25))
    assert authority.deadline == 30
    with pytest.raises(DispatchDenied):
        authority.renew(USE, LeaseDeadline(9))
    assert authority.deadline == 30


async def test_fence_waits_for_admitted_write_and_rejects_queued_write() -> None:
    authority = DispatchAuthority(USE, LeaseDeadline(30), clock=lambda: 10)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def write() -> None:
        async with authority.write(USE):
            entered.set()
            await release.wait()

    active = asyncio.create_task(write())
    await entered.wait()
    queued = asyncio.create_task(write())
    fence = asyncio.create_task(authority.fence())
    await asyncio.sleep(0)
    assert not fence.done()
    with pytest.raises(DispatchDenied):
        authority.renew(USE, LeaseDeadline(40))
    release.set()
    await active
    with pytest.raises(DispatchDenied):
        await queued
    await fence
    with pytest.raises(DispatchDenied):
        authority.check(USE)


async def test_stalled_write_expires_without_blocking_fence_forever() -> None:
    loop = asyncio.get_running_loop()
    authority = DispatchAuthority(USE, LeaseDeadline(loop.time() + 0.05), clock=loop.time)
    entered = asyncio.Event()

    async def stalled_write() -> None:
        async with authority.write(USE):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(stalled_write())
    await entered.wait()
    async with asyncio.timeout(1):
        await authority.fence()
    with pytest.raises(TimeoutError):
        await task


async def test_cancelled_write_releases_gate_and_preserves_cancellation() -> None:
    authority = DispatchAuthority(USE, LeaseDeadline(20), clock=lambda: 10)
    entered = asyncio.Event()

    async def cancelled_write() -> None:
        async with authority.write(USE):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_write())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(1):
        await authority.fence()
