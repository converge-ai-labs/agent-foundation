"""Request-local Modal transport reuse preserves guest fencing and cleanup."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import modal
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.providers.environments.modal import provider
from a13n_service.providers.environments.modal.configuration import ModalRecipe
from a13n_service.runs.desktop_assistance import service

GENERATION = "a" * 32
REQUESTS = (
    wire.Describe(),
    wire.Renew(generation=GENERATION, id="dgrant_test", epoch=3, ttl_seconds=60),
    wire.Grant(generation=GENERATION, id="dgrant_test", epoch=3, mode="control", ttl_seconds=60),
)
HANDBACK = (
    wire.Inspect(operation="revoke", generation=GENERATION, id="dgrant_test", epoch=3),
    wire.Inspect(operation="revoke", generation=GENERATION, id="dviewer_test", epoch=4),
    wire.Settle(generation=GENERATION),
)


@pytest.fixture
def cloud(monkeypatch):
    recipe = ModalRecipe(image="im-envd", desktop=True, desktop_assistance=True)
    state = EnvironmentState(
        provider_key="modal",
        state_version="1",
        state={
            "target_id": "sb-fixed",
            "environment_id": "env-fixed",
            "configuration_fingerprint": recipe.fingerprint,
        },
    )
    fixture = SimpleNamespace(
        recipe=recipe,
        state=state,
        clients=[],
        sandboxes=[],
        calls=[],
        failure=None,
        failure_operation="renew",
        invalid_reply=None,
        mismatched_tags=False,
        cleanup_failure=False,
    )

    class Client:
        def __init__(self, *args):
            self.closed = False
            fixture.clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await anyio.sleep(0)  # Cleanup must remain runnable under cancellation.
            self.closed = True

    async def from_id(identity, *, client):
        assert identity == "sb-fixed" and not client.closed

        async def execute(*argv, **kwargs):
            request = next(r for r in (*REQUESTS, *HANDBACK) if wire.command(r) == list(argv))
            fixture.calls.append((target, request))
            if request.operation == fixture.failure_operation and fixture.failure == "timeout":
                await anyio.sleep_forever()
            if request.operation == fixture.failure_operation and fixture.failure:
                envelope = wire.Envelope(error=fixture.failure)
            elif isinstance(request, wire.Describe):
                envelope = wire.Envelope(
                    result=wire.Description(
                        protocol_version=1,
                        generation=GENERATION,
                        port=6081,
                        max_ttl_seconds=60,
                        read_only_enforced=True,
                        revocation_fences_grants=True,
                    )
                )
            elif isinstance(request, wire.Settle):
                envelope = wire.Envelope(result=wire.Settlement(generation=GENERATION, input_cleanup_complete=True))
            else:
                envelope = wire.Envelope(
                    result=wire.AccessGrant(
                        id=request.id,
                        generation=request.generation,
                        mode="control",
                        epoch=request.epoch,
                        state="revoked" if isinstance(request, wire.Inspect) else "active",
                        remaining_seconds=0 if isinstance(request, wire.Inspect) else 60,
                        token="fixture-token" if isinstance(request, wire.Grant) else None,
                        password="fixture-password" if isinstance(request, wire.Grant) else None,
                    )
                )
            raw = envelope.model_dump(mode="json")
            if request.operation == fixture.failure_operation and fixture.invalid_reply is not None:
                raw["result"].update(fixture.invalid_reply)
            return SimpleNamespace(
                stdout=SimpleNamespace(read=SimpleNamespace(aio=AsyncMock(return_value=json.dumps(raw)))),
                wait=SimpleNamespace(aio=AsyncMock()),
                returncode=0,
            )

        target = SimpleNamespace(
            object_id=identity,
            get_tags=SimpleNamespace(
                aio=AsyncMock(
                    return_value={
                        "a13n_environment": "wrong-owner" if fixture.mismatched_tags else "env-fixed",
                        "a13n_configuration": recipe.fingerprint,
                    }
                )
            ),
            poll=SimpleNamespace(aio=AsyncMock(return_value=None)),
            exec=SimpleNamespace(aio=AsyncMock(side_effect=execute)),
            tunnels=SimpleNamespace(aio=AsyncMock(return_value={6081: SimpleNamespace(url="https://desktop.example")})),
            detach=SimpleNamespace(
                aio=AsyncMock(side_effect=RuntimeError("detach failed") if fixture.cleanup_failure else None)
            ),
        )
        fixture.sandboxes.append(target)
        return target

    monkeypatch.setattr(modal, "Client", Client)
    fixture.lookup = AsyncMock(side_effect=from_id)
    monkeypatch.setattr(modal.Sandbox, "from_id", SimpleNamespace(aio=fixture.lookup))

    async def open_provider(runtime, target):
        return await provider(
            configuration=ModalConnectionConfiguration(workspace="test", app_name="test"),
            credential=ModalCredential(token_id="fixture-id", token_secret="fixture-secret"),
            runtime=None,
        )

    monkeypatch.setattr(service, "open_provider", open_provider)
    fixture.target = SimpleNamespace(recipe=recipe.model_dump(), environment_id="env-fixed", state=state)
    return fixture


@pytest.mark.anyio
async def test_request_reuses_one_validated_target_but_next_request_is_independent(cloud):
    for identity in ("dtop-first", "dtop-second"):
        async with service.access_dispatch(None, cloud.target, operation_id=identity) as send:
            for request in REQUESTS:
                await send(request)
            assert not cloud.clients[-1].closed
        assert cloud.clients[-1].closed
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 2
    assert cloud.sandboxes[0] is not cloud.sandboxes[1]
    for target in cloud.sandboxes:
        target.get_tags.aio.assert_awaited_once()
        target.poll.aio.assert_awaited_once()
        target.detach.aio.assert_awaited_once()
        assert [request for sandbox, request in cloud.calls if sandbox is target] == list(REQUESTS)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["stale_generation", "stale_epoch", "endpoint_unavailable", "timeout"])
async def test_failed_or_cancelled_renewal_cleans_up_without_replacement_or_grant(cloud, failure):
    cloud.failure = failure
    error = TimeoutError if failure == "timeout" else EnvironmentProviderError
    with pytest.raises(error):
        with anyio.fail_after(0.1):
            async with service.access_dispatch(None, cloud.target, operation_id="dtop-failed") as send:
                for request in REQUESTS:
                    await send(request)
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 1
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()

    assert [request.operation for _, request in cloud.calls] == ["describe", "renew"]


@pytest.fixture
def continuation(cloud, monkeypatch):
    row = SimpleNamespace(state="consumed", successor_id="run-successor", generation=GENERATION)
    session = SimpleNamespace(get=AsyncMock(return_value=row))
    active = False

    @asynccontextmanager
    async def transaction(storage):
        nonlocal active
        active = True
        try:
            yield session
        finally:
            active = False

    original_lookup = cloud.lookup.side_effect

    async def lookup(*args, **kwargs):
        assert not active, "Provider I/O must run outside the transaction"
        return await original_lookup(*args, **kwargs)

    cloud.lookup.side_effect = lookup
    monkeypatch.setattr(service, "transaction", transaction)
    monkeypatch.setattr(service, "lock_lease", AsyncMock())
    monkeypatch.setattr(service, "lock", AsyncMock(return_value=SimpleNamespace(holder_id="dassist-test")))
    return SimpleNamespace(
        row=row, runtime=SimpleNamespace(storage=None), lease=SimpleNamespace(run_id="run-successor")
    )


@pytest.mark.anyio
async def test_continuation_reuses_target_only_within_each_check(cloud, continuation):
    for _ in range(2):
        await service.verify_continuation(continuation.runtime, continuation.lease, cloud.target)
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 2
    for client, target in zip(cloud.clients, cloud.sandboxes, strict=True):
        assert client.closed
        target.get_tags.aio.assert_awaited_once()
        target.poll.aio.assert_awaited_once()
        target.detach.aio.assert_awaited_once()
        assert [request.operation for sandbox, request in cloud.calls if sandbox is target] == ["describe", "settle"]


@pytest.mark.anyio
@pytest.mark.parametrize("generation", [None, "b" * 32])
async def test_continuation_generation_conflict_keeps_service_error_and_skips_settle(cloud, continuation, generation):
    continuation.row.generation = generation
    with pytest.raises(ServiceError) as error:
        await service.verify_continuation(continuation.runtime, continuation.lease, cloud.target)
    assert error.value.details["reason"] == "desktop_generation_changed"
    assert [request.operation for _, request in cloud.calls] == ["describe"]
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [("state", "human_control"), ("successor_id", "run-other")])
async def test_continuation_only_checks_consumed_assistance_for_this_successor(cloud, continuation, field, value):
    setattr(continuation.row, field, value)
    await service.verify_continuation(continuation.runtime, continuation.lease, cloud.target)
    assert not cloud.clients


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation,failure,invalid_reply",
    [
        ("describe", "endpoint_unavailable", None),
        ("describe", "timeout", None),
        ("settle", "stale_generation", None),
        ("settle", None, {"input_cleanup_complete": False}),
        ("settle", None, {"generation": "b" * 32}),
        ("settle", "timeout", None),
    ],
)
async def test_continuation_failure_or_timeout_cleans_up_and_blocks_resume(
    cloud, continuation, operation, failure, invalid_reply
):
    cloud.failure_operation, cloud.failure, cloud.invalid_reply = operation, failure, invalid_reply
    with pytest.raises(TimeoutError if failure == "timeout" else EnvironmentProviderError):
        with anyio.fail_after(0.1):
            await service.verify_continuation(continuation.runtime, continuation.lease, cloud.target)
    assert len(cloud.clients) == cloud.lookup.await_count == 1
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()
    assert [request.operation for _, request in cloud.calls] == (
        ["describe"] if operation == "describe" else ["describe", "settle"]
    )


@pytest.mark.anyio
async def test_continuation_cleanup_failure_blocks_resume_and_closes_client(cloud, continuation):
    cloud.cleanup_failure = True
    with pytest.raises(EnvironmentProviderError):
        await service.verify_continuation(continuation.runtime, continuation.lease, cloud.target)
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()


@pytest.mark.anyio
async def test_wrong_target_owner_is_rejected_before_any_guest_command(cloud):
    cloud.mismatched_tags = True
    with pytest.raises(EnvironmentProviderError):
        async with service.access_dispatch(None, cloud.target, operation_id="dtop-owner") as send:
            await send(REQUESTS[0])
    assert not cloud.calls
    cloud.sandboxes[0].poll.aio.assert_not_awaited()
    assert cloud.clients[0].closed


@pytest.mark.anyio
async def test_other_providers_keep_per_operation_dispatch(monkeypatch):
    dispatch = AsyncMock()
    open_provider = AsyncMock(side_effect=AssertionError("Scoped opening is Modal-only"))
    monkeypatch.setattr(service, "dispatch", dispatch)
    monkeypatch.setattr(service, "open_provider", open_provider)
    target = SimpleNamespace(state=EnvironmentState(provider_key="e2b", state_version="1", state={}))
    async with service.access_dispatch(None, target, operation_id="dtop-other") as send:
        for request in (*REQUESTS, *HANDBACK):
            await send(request)
    assert [call.args[2] for call in dispatch.await_args_list] == [*REQUESTS, *HANDBACK]
    assert all(call.kwargs == {"operation_id": "dtop-other"} for call in dispatch.await_args_list)
    open_provider.assert_not_awaited()


@pytest.mark.anyio
async def test_handback_revokes_every_grant_then_settles_on_one_validated_target(cloud):
    async with service.access_dispatch(None, cloud.target, operation_id="dtop-handback") as send:
        for request in HANDBACK:
            await send(request)
        assert not cloud.clients[0].closed
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 1
    target = cloud.sandboxes[0]
    assert [request for sandbox, request in cloud.calls if sandbox is target] == list(HANDBACK)
    target.get_tags.aio.assert_awaited_once()
    target.poll.aio.assert_awaited_once()
    target.detach.aio.assert_awaited_once()
    assert cloud.clients[0].closed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "failure", "invalid_reply"),
    [
        ("revoke", "stale_epoch", None),
        ("revoke", "stale_generation", None),
        ("revoke", None, {"state": "active"}),
        ("revoke", "timeout", None),
        ("settle", None, {"input_cleanup_complete": False}),
        ("settle", None, {"generation": "b" * 32}),
        ("settle", "timeout", None),
    ],
)
async def test_handback_failure_stops_sequence_and_releases_the_pinned_target(cloud, operation, failure, invalid_reply):
    cloud.failure_operation, cloud.failure, cloud.invalid_reply = operation, failure, invalid_reply
    with pytest.raises(TimeoutError if failure == "timeout" else EnvironmentProviderError):
        with anyio.fail_after(0.1):
            async with service.access_dispatch(None, cloud.target, operation_id="dtop-handback-failure") as send:
                for request in HANDBACK:
                    await send(request)
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 1
    expected = list(HANDBACK[:1]) if operation == "revoke" else list(HANDBACK)
    assert [request for _, request in cloud.calls] == expected
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()
