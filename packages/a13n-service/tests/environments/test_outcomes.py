"""Persist Provider outcomes independently from interrupted or ambiguous publication."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentState
from a13n_service.environments.domain import CreateManagedEnvironmentRequest, EnvironmentCommandRequest
from a13n_service.environments.identity import target_identity
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import (
    EnvironmentCommandRecord,
    EnvironmentRecord,
    EnvironmentTemplateRevisionRecord,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from .conftest import WORKSPACE_ID, actor, catalog_with
from .test_lifecycle import Target, fixture_environment

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 9, 12, tzinfo=UTC)


def unavailable():
    return DBAPIError(None, None, RuntimeError("Publication unavailable"), connection_invalidated=True)


def interrupt_publication(monkeypatch, lifecycle, *, after_commit=False):
    publish = lifecycle.publish
    attempts = []

    async def interrupted(operation, outcome):
        attempts.append(outcome)
        if len(attempts) == 1:
            if after_commit:
                await publish(operation, outcome)
            raise unavailable()
        return await publish(operation, outcome)

    monkeypatch.setattr(lifecycle, "publish", interrupted)
    return attempts


@pytest.mark.parametrize("action", ["stop", "delete"])
@pytest.mark.parametrize("after_commit", [False, True])
async def test_successful_effect_survives_interrupted_publication(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, action, after_commit
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events = []
    state = EnvironmentState(provider_key="docker", state_version="1", state={"target": "same"})

    async def construct(operation):
        return Target(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.state, row.generation = "running", state.model_dump(mode="json"), 3
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action=action),
        idempotency_key=action,
    )
    attempts = interrupt_publication(monkeypatch, lifecycle, after_commit=after_commit)
    await lifecycle.maintain(environment.id)
    assert events == [action, "close"]
    assert len(attempts) == 2 and attempts[0] is attempts[1]
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        receipt = await session.get(EnvironmentCommandRecord, command.id)
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert row.status == ("stopped" if action == "stop" else "deleted")
        assert row.state == (state.model_dump(mode="json") if action == "stop" else None)
        assert row.generation == 3 and row.operation_id is None and row.last_error is None
        assert receipt.status == "completed" and receipt.completed_at is not None
        assert len(audits) == 1 and audits[0].outcome == "success"
        assert audits[0].id == attempts[0].publication_id


@pytest.mark.parametrize("previous_claim", [False, True])
@pytest.mark.parametrize("failure_at", ["construction", "authorization", "provider_validation"])
async def test_undispatched_failure_preserves_only_an_earlier_unknown_operation(
    environment_service,
    environment_sessions,
    provider_catalog,
    protector,
    tmp_path,
    monkeypatch,
    previous_claim,
    failure_at,
):
    environment = await fixture_environment(environment_service)
    now = NOW
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: now)
    events = []

    class InvalidTarget(Target):
        async def _stop(self):
            raise EnvironmentProviderError(
                "Rejected before dispatch",
                code="provider_conflict",
                category=Category.CONFLICT,
                certainty=Certainty.NOT_DISPATCHED,
            )

    async def construct(operation):
        if failure_at == "construction":
            raise ValueError("Construction rejected before dispatch")
        return (
            InvalidTarget(operation.state, events)
            if failure_at == "provider_validation"
            else Target(operation.state, events)
        )

    async def reject(operation):
        raise ValueError("Authorization rejected before dispatch")

    monkeypatch.setattr(lifecycle, "construct", construct)
    if failure_at == "authorization":
        monkeypatch.setattr(lifecycle, "validate_operation", reject)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = "running"
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop",
    )
    if previous_claim:
        async with transaction(environment_sessions) as session:
            row = await session.get(EnvironmentRecord, environment.id)
            row.operation_owner = "envowner-interrupted"
            row.operation_expires_at = now - timedelta(seconds=1)
    with pytest.raises(Exception, match=r"[Rr]ejected before dispatch"):
        await lifecycle.maintain(environment.id)
    assert events == ([] if failure_at == "construction" else ["close"])
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        receipt = await session.get(EnvironmentCommandRecord, command.id)
        assert row.status == "running"
        assert row.operation_id == (command.id if previous_claim else None)
        assert receipt.status == ("pending" if previous_claim else "failed")
        assert row.last_error["code"] == (
            "environment_operation_unresolved" if previous_claim else "environment_operation_failed"
        )
    if previous_claim:

        async def recovered(operation):
            assert operation.operation_id == command.id
            return Target(operation.state, events)

        monkeypatch.setattr(lifecycle, "construct", recovered)
        monkeypatch.setattr(lifecycle, "validate_operation", EnvironmentLifecycle.validate_operation.__get__(lifecycle))
        now += timedelta(seconds=71)
        await lifecycle.maintain(environment.id)
        async with short_session(environment_sessions) as session:
            assert (await session.get(EnvironmentCommandRecord, command.id)).status == "completed"
            assert (await session.get(EnvironmentRecord, environment.id)).status == "stopped"


async def test_unpublished_success_retains_the_pending_operation(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events, publications = [], []

    async def construct(operation):
        return Target(operation.state, events)

    async def unavailable_publication(operation, outcome):
        publications.append(outcome)
        raise unavailable()

    monkeypatch.setattr(lifecycle, "construct", construct)
    monkeypatch.setattr(lifecycle, "publish", unavailable_publication)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = "running"
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop",
    )
    with pytest.raises(DBAPIError):
        await lifecycle.maintain(environment.id)
    assert events == ["stop", "close"]
    assert len(publications) == 2 and publications[0] is publications[1]
    assert publications[0].succeeded
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.operation_id == command.id and row.status == "running"
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "pending"
        assert not tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )


@pytest.mark.parametrize("after_commit", [False, True])
async def test_insufficient_renewal_keeps_observed_expiry_after_publication_retry(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, after_commit
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events = []
    expiry = NOW + timedelta(seconds=20)

    class ShortRenewal(Target):
        async def keepalive(self, *, deadline, operation_id):
            events.append("keepalive")
            return expiry

    async def construct(operation):
        return ShortRenewal(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    lifecycle.catalog = catalog_with(provider_catalog, "docker", requires_keepalive=True)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since = "running", NOW
    attempts = interrupt_publication(monkeypatch, lifecycle, after_commit=after_commit)
    with pytest.raises(EnvironmentError) as error:
        await lifecycle.maintain(environment.id)
    assert error.value.code == "environment_keepalive_failed"
    assert events == ["keepalive", "close"]
    assert attempts[0] is attempts[1] and attempts[0].resolved and not attempts[0].succeeded
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.expires_at.replace(tzinfo=UTC) == expiry
        assert row.status == "running" and row.operation_id is None
        assert row.last_error["code"] == "environment_operation_failed"
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert len(audits) == 1 and audits[0].outcome == "failure"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_conflicting_target_is_not_adopted_and_preserves_cancellation(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, cancelled
):
    first = await fixture_environment(environment_service)
    async with short_session(environment_sessions) as session:
        revision = await session.get(EnvironmentTemplateRevisionRecord, first.template_revision_id)
        template_id = revision.template_id
    second = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateManagedEnvironmentRequest(template_id=template_id),
        idempotency_key="second",
    )
    catalog = catalog_with(provider_catalog, "docker", target_identity=lambda **kwargs: "same-target")
    identity = target_identity(catalog.require("docker"), {}, "same-target")
    lifecycle = EnvironmentLifecycle(environment_sessions, catalog, protector, clock=lambda: NOW)
    events = []
    state = EnvironmentState(provider_key="docker", state_version="1", state={"target": "same-target"})

    class ObservedTarget(Target):
        async def reconcile(self):
            events.append("observe")
            if cancelled:
                raise asyncio.CancelledError("Observation cancelled")
            return "running"

    async def construct(operation):
        return ObservedTarget(state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, first.id)
        row.status, row.target_identity, row.generation = "running", identity, 1
        other = await session.get(EnvironmentRecord, second.id)
        other.status, other.operation_id, other.operation_action = "unavailable", "envop-interrupted", "prepare"
        other.operation_expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(asyncio.CancelledError if cancelled else EnvironmentError) as error:
        await lifecycle.maintain(second.id)
    if not cancelled:
        assert error.value.code == "environment_target_conflict"
    assert events == ["observe", "close"]
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, second.id)
        assert row.status == "unavailable" and row.target_identity is None
        assert row.state == state.model_dump(mode="json")
        assert row.operation_id == ("envop-interrupted" if cancelled else None)
        assert row.generation == 0
        assert (await session.get(EnvironmentRecord, first.id)).target_identity == identity


async def test_close_failure_does_not_rewrite_a_completed_stop(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events = []

    class BrokenClose(Target):
        async def _close(self):
            raise RuntimeError("Local cleanup failed")

    async def construct(operation):
        return BrokenClose(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = "running"
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop",
    )
    with pytest.raises(RuntimeError, match="Local cleanup failed"):
        await lifecycle.maintain(environment.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "stopped" and row.operation_id is None and row.last_error is None
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "completed"
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert len(audits) == 1 and audits[0].outcome == "success"
    assert events == ["stop"]


async def test_receipt_replay_cannot_overwrite_a_later_operation(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events = []

    async def construct(operation):
        return Target(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = "running"
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop",
    )
    publish = lifecycle.publish
    attempts = []

    async def commit_then_advance(operation, outcome):
        attempts.append(outcome)
        generation = await publish(operation, outcome)
        if len(attempts) == 1:
            async with transaction(environment_sessions) as session:
                row = await session.get(EnvironmentRecord, environment.id)
                row.status, row.generation = "running", 2
                row.operation_id, row.operation_action, row.operation_owner = "envop-new", "prepare", "envowner-new"
                row.operation_generation += 1
                row.operation_expires_at = NOW + timedelta(seconds=70)
            raise unavailable()
        return generation

    monkeypatch.setattr(lifecycle, "publish", commit_then_advance)
    await lifecycle.maintain(environment.id)
    assert len(attempts) == 2 and attempts[0] is attempts[1]
    assert events == ["stop", "close"]
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "running" and row.generation == 2 and row.operation_id == "envop-new"
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "completed"
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert len(audits) == 1


async def test_publication_cancellation_is_not_replaced_by_the_provider_error(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    events = []

    class MissingTarget(Target):
        async def _stop(self):
            raise EnvironmentProviderError(
                "Target is missing", code="provider_missing", category=Category.MISSING, certainty=Certainty.KNOWN
            )

    async def construct(operation):
        return MissingTarget(operation.state, events)

    async def cancelled_publication(operation, outcome):
        raise asyncio.CancelledError("Publication cancelled")

    monkeypatch.setattr(lifecycle, "construct", construct)
    monkeypatch.setattr(lifecycle, "publish", cancelled_publication)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = "running"
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop",
    )
    with pytest.raises(asyncio.CancelledError, match="Publication cancelled"):
        await lifecycle.maintain(environment.id)
    assert events == ["close"]
    async with short_session(environment_sessions) as session:
        assert (await session.get(EnvironmentRecord, environment.id)).operation_id == command.id
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "pending"
