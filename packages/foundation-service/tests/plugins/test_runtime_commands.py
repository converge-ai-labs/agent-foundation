from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord, UserRecord
from a13n_service.plugins.commands import (
    PluginRuntimeCatalogSnapshot,
    PluginRuntimeCommand,
    PluginRuntimeCommandFailure,
    PluginRuntimeVersionSpec,
)
from a13n_service.plugins.errors import PluginError
from a13n_service.plugins.models import PluginRecord, PluginRuntimeStateRecord
from a13n_service.plugins.runtime import (
    PluginRuntimeLock,
    PluginRuntimeLockError,
    PluginRuntimeLockStore,
    WorkerReleaseManifest,
    default_runtime_target,
)
from a13n_service.plugins.runtime_commands import PluginRuntimeCommandCoordinator
from a13n_service.plugins.service import PluginService
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import BUILDER_ID, NOW, actor, build_wheel
from .test_service import _upload


@dataclass(frozen=True, slots=True)
class _Contribution:
    plugin_id: str
    plugin_version_id: str
    plugin_key: str
    distribution_name: str
    version: str
    top_level_package: str
    wheel_digest: str
    artifact_ref: str
    requires_dist: tuple[str, ...]

    @classmethod
    def from_spec(cls, spec: PluginRuntimeVersionSpec) -> _Contribution:
        return cls(
            plugin_id=spec.plugin.id,
            plugin_version_id=spec.version.id,
            plugin_key=spec.plugin.plugin_key,
            distribution_name=spec.plugin.distribution_name,
            version=spec.version.version,
            top_level_package=spec.plugin.top_level_package,
            wheel_digest=spec.version.content_digest,
            artifact_ref=spec.version.artifact_ref,
            requires_dist=spec.version.requires_dist,
        )


class _CandidateResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._store = PluginRuntimeLockStore(
            WorkerReleaseManifest(
                worker_release="test-worker",
                harness_version="test-harness",
                runtime_target=default_runtime_target(),
                distributions={},
            ),
            clock=lambda: NOW,
        )
        self.calls: list[tuple[str, PluginRuntimeCommand]] = []
        self.require_failures = 0

    async def resolve_candidate(
        self,
        *,
        operation_id: str,
        command: PluginRuntimeCommand,
        catalog: PluginRuntimeCatalogSnapshot,
    ) -> PluginRuntimeLock:
        selected = {item.plugin.id: item for item in catalog.active_versions}
        if command == "activate":
            assert catalog.target_version is not None
            selected[catalog.target_plugin.id] = catalog.target_version
        else:
            selected.pop(catalog.target_plugin.id, None)
        self.calls.append((operation_id, command))
        async with transaction(self._sessions) as session:
            return await self._store.build_and_persist(
                session,
                mode="runner",
                plugins=tuple(_Contribution.from_spec(item) for item in selected.values()),
            )

    async def require_candidate(self, *, runtime_lock_digest: str) -> PluginRuntimeLock:
        if self.require_failures:
            self.require_failures -= 1
            raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
        async with short_session(self._sessions) as session:
            return await self._store.require(session, runtime_lock_digest, mode="runner")


class _StagingAuthority:
    def __init__(self) -> None:
        self.staged: list[tuple[str, str]] = []
        self.activated: list[tuple[str, str, int]] = []
        self.aborted: list[tuple[str, str | None]] = []
        self.stage_failure: PluginRuntimeCommandFailure | None = None
        self.activate_failures = 0

    async def stage_candidate(self, *, operation_id: str, runtime_lock: PluginRuntimeLock) -> str:
        self.staged.append((operation_id, runtime_lock.digest))
        if self.stage_failure is not None:
            raise self.stage_failure
        return f"staged-{operation_id}"

    async def activate_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str,
        runtime_generation: int,
    ) -> None:
        assert staging_token == f"staged-{operation_id}"
        self.activated.append((operation_id, runtime_lock.digest, runtime_generation))
        if self.activate_failures:
            self.activate_failures -= 1
            raise PluginRuntimeCommandFailure(
                SafeFailure(code="worker_ack_pending", message="A staged Worker has not acknowledged cutover."),
                retryable=True,
            )

    async def abort_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str | None,
    ) -> None:
        del runtime_lock
        self.aborted.append((operation_id, staging_token))


@pytest.fixture
def runtime_coordinator(
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority]:
    resolver = _CandidateResolver(plugin_sessions)
    authority = _StagingAuthority()
    return (
        PluginRuntimeCommandCoordinator(
            plugin_sessions,
            resolver,
            authority,
            lease_seconds=30,
            clock=lambda: NOW,
        ),
        resolver,
        authority,
    )


async def _activate(
    service: PluginService,
    coordinator: PluginRuntimeCommandCoordinator,
    *,
    version: str,
    key: str,
    plugin_id: str | None = None,
):
    uploaded = await _upload(
        service,
        build_wheel(version=version),
        key=f"upload-{key}",
        plugin_id=plugin_id,
    )
    plugin = await service.get(actor=actor(), plugin_id=uploaded.version.plugin_id)
    receipt = await coordinator.activate(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        plugin=plugin,
        plugin_version=uploaded.version,
        idempotency_key=key,
    )
    return uploaded, receipt


@pytest.mark.anyio
async def test_activate_replays_receipt_and_commits_catalog_after_worker_ack(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, resolver, authority = runtime_coordinator
    uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-v1",
    )
    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=uploaded.version.plugin_id)
    replay = await coordinator.activate(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        plugin=plugin,
        plugin_version=uploaded.version,
        idempotency_key="activate-v1",
    )

    assert accepted == replay
    assert accepted.status == "running"
    assert await coordinator.reconcile_once() is True

    completed = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert completed.status == "succeeded"
    assert [item.resource_type for item in completed.result_refs] == ["plugin", "plugin_version"]
    assert resolver.calls == [(accepted.operation_id, "activate")]
    assert authority.staged[0][0] == accepted.operation_id
    assert authority.activated[0][0] == accepted.operation_id
    async with short_session(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        current = await session.get(PluginRecord, uploaded.version.plugin_id)
        audit_actions = set(
            (
                await session.scalars(
                    select(SecurityAuditRecord.action).where(
                        SecurityAuditRecord.resource_id == uploaded.version.plugin_id,
                        SecurityAuditRecord.action.like("plugin_runtime.activate.%"),
                    )
                )
            ).all()
        )
        assert state is not None and state.active_lock_digest == authority.staged[0][1]
        assert state.runtime_generation == 2
        assert current is not None and current.active_version_id == uploaded.version.id
        assert audit_actions == {
            "plugin_runtime.activate.accepted",
            "plugin_runtime.activate.succeeded",
        }


@pytest.mark.anyio
async def test_reactivating_active_version_reuses_lock_without_worker_staging(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, authority = runtime_coordinator
    uploaded, initial = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-initial-version",
    )
    assert await coordinator.reconcile_once() is True
    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=uploaded.version.plugin_id)
    repeated = await coordinator.activate(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        plugin=plugin,
        plugin_version=uploaded.version,
        idempotency_key="reactivate-current-version",
    )

    assert await coordinator.reconcile_once() is True
    receipt = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=repeated.operation_id,
    )
    assert receipt.status == "succeeded"
    assert len(authority.staged) == 1
    assert len(authority.activated) == 1
    async with short_session(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        assert state is not None and state.runtime_generation == 2
        assert state.active_lock_digest == authority.staged[0][1]
    assert initial.operation_id != repeated.operation_id


@pytest.mark.anyio
async def test_stage_failure_keeps_old_catalog_and_returns_safe_failure(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, authority = runtime_coordinator
    first, initial = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-initial",
    )
    assert await coordinator.reconcile_once() is True
    authority.stage_failure = PluginRuntimeCommandFailure(
        SafeFailure(code="worker_stage_failed", message="A serviceable Worker rejected the candidate Runtime.")
    )
    second, rejected = await _activate(
        runner_plugin_service,
        coordinator,
        version="2.0.0",
        key="activate-rejected",
        plugin_id=first.version.plugin_id,
    )

    assert await coordinator.reconcile_once() is True
    receipt = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=rejected.operation_id,
    )
    assert receipt.status == "failed"
    assert receipt.error is not None and receipt.error.code == "worker_stage_failed"
    assert authority.aborted[-1][0] == rejected.operation_id
    async with short_session(plugin_sessions) as session:
        current = await session.get(PluginRecord, first.version.plugin_id)
        failed_audit = await session.scalar(
            select(SecurityAuditRecord).where(
                SecurityAuditRecord.resource_id == first.version.plugin_id,
                SecurityAuditRecord.action == "plugin_runtime.activate.failed",
            )
        )
        assert current is not None and current.active_version_id == first.version.id
        assert current.active_version_id != second.version.id
        assert failed_audit is not None and failed_audit.outcome == "failure"
    assert initial.operation_id != rejected.operation_id


@pytest.mark.anyio
async def test_candidate_lost_before_staging_fails_without_changing_catalog(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, resolver, authority = runtime_coordinator
    resolver.require_failures = 1
    uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="candidate-lost-before-staging",
    )

    assert await coordinator.reconcile_once() is True
    receipt = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert receipt.status == "failed"
    assert receipt.error is not None and receipt.error.code == "plugin_runtime_incompatible"
    assert authority.staged == []
    assert authority.aborted == []
    async with short_session(plugin_sessions) as session:
        current = await session.get(PluginRecord, uploaded.version.plugin_id)
        assert current is not None and current.active_version_id is None


@pytest.mark.anyio
async def test_post_commit_ack_failure_retries_without_rolling_back_catalog(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, authority = runtime_coordinator
    authority.activate_failures = 1
    uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-with-delayed-ack",
    )

    assert await coordinator.reconcile_once() is True
    running = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert running.status == "running"
    async with short_session(plugin_sessions) as session:
        current = await session.get(PluginRecord, uploaded.version.plugin_id)
        assert current is not None and current.active_version_id == uploaded.version.id

    assert await coordinator.reconcile_once() is True
    succeeded = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert succeeded.status == "succeeded"
    assert len(authority.activated) == 2


@pytest.mark.anyio
async def test_committed_candidate_loss_remains_retryable(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
) -> None:
    coordinator, resolver, authority = runtime_coordinator
    authority.activate_failures = 1
    _uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="committed-candidate-temporarily-missing",
    )
    assert await coordinator.reconcile_once() is True
    resolver.require_failures = 1

    assert await coordinator.reconcile_once() is True
    running = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert running.status == "running"

    assert await coordinator.reconcile_once() is True
    succeeded = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert succeeded.status == "succeeded"


@pytest.mark.anyio
async def test_runtime_command_authorization_and_idempotency_conflict(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    first, _accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="same-command-key",
    )
    second = await _upload(
        runner_plugin_service,
        build_wheel(version="2.0.0"),
        key="upload-second-version",
        plugin_id=first.version.plugin_id,
    )
    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=first.version.plugin_id)

    with pytest.raises(PluginError) as conflict:
        await coordinator.activate(
            actor=actor(),
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            plugin=plugin,
            plugin_version=second.version,
            idempotency_key="same-command-key",
        )
    with pytest.raises(PluginError) as forbidden:
        await coordinator.activate(
            actor=actor(BUILDER_ID),
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            plugin=plugin,
            plugin_version=second.version,
            idempotency_key="builder-command",
        )

    assert conflict.value.code == "plugin_idempotency_conflict"
    assert forbidden.value.code == "forbidden"


@pytest.mark.anyio
async def test_runtime_command_rejects_non_ascii_idempotency_keys(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    with pytest.raises(PluginError) as caught:
        await _activate(runner_plugin_service, coordinator, version="1.0.0", key="运行-🔁")
    assert caught.value.code == "invalid_request"


@pytest.mark.anyio
async def test_runtime_command_evidence_expires_at_the_exact_ttl_boundary(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    first_version, first = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="runtime-expiry-boundary",
    )
    second_version = await _upload(
        runner_plugin_service,
        build_wheel(version="2.0.0"),
        key="upload-runtime-expiry-second",
        plugin_id=first_version.version.plugin_id,
    )
    async with transaction(plugin_sessions) as session:
        evidence = await session.scalar(
            select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "plugin_runtime.activate")
        )
        assert evidence is not None
        evidence.created_at = NOW - timedelta(hours=24)
        evidence.expires_at = NOW

    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=first_version.version.plugin_id)
    second = await coordinator.activate(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        plugin=plugin,
        plugin_version=second_version.version,
        idempotency_key="runtime-expiry-boundary",
    )

    assert second.operation_id != first.operation_id


@pytest.mark.anyio
async def test_deactivate_commits_empty_catalog_without_rewriting_version(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, resolver, authority = runtime_coordinator
    uploaded, activated = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-before-deactivate",
    )
    assert await coordinator.reconcile_once() is True
    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=uploaded.version.plugin_id)
    accepted = await coordinator.deactivate(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        plugin=plugin,
        idempotency_key="deactivate-plugin",
    )

    assert await coordinator.reconcile_once() is True
    receipt = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert receipt.status == "succeeded"
    assert [item.resource_type for item in receipt.result_refs] == ["plugin"]
    assert authority.activated[-1][0] == accepted.operation_id
    async with short_session(plugin_sessions) as session:
        current = await session.get(PluginRecord, uploaded.version.plugin_id)
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        assert current is not None and current.active_version_id is None
        assert state is not None and state.runtime_generation == 3 and state.active_lock_digest is not None
        lock = await resolver.require_candidate(runtime_lock_digest=state.active_lock_digest)
        assert lock.plugins == ()
    assert activated.operation_id != accepted.operation_id


@pytest.mark.anyio
async def test_required_plugin_cannot_be_deactivated(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    uploaded, _activated = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="activate-required-plugin",
    )
    assert await coordinator.reconcile_once() is True
    async with transaction(plugin_sessions) as session:
        record = await session.get(PluginRecord, uploaded.version.plugin_id, with_for_update=True)
        assert record is not None
        record.required = True
    plugin = await runner_plugin_service.get(actor=actor(), plugin_id=uploaded.version.plugin_id)
    with pytest.raises(PluginError) as rejected:
        await coordinator.deactivate(
            actor=actor(),
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            plugin=plugin,
            idempotency_key="deactivate-required-plugin",
        )

    assert rejected.value.code == "plugin_state_conflict"
    async with short_session(plugin_sessions) as session:
        current = await session.get(PluginRecord, uploaded.version.plugin_id)
        assert current is not None and current.active_version_id == uploaded.version.id


@pytest.mark.anyio
async def test_receipt_is_scoped_to_original_authorized_caller(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    _uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="caller-scoped-receipt",
    )
    other_id = "usr_otheradmin1234567"
    async with transaction(plugin_sessions) as session:
        session.add(
            UserRecord(
                id=other_id,
                email="other@example.com",
                normalized_email="other@example.com",
                name="Other Admin",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            RoleBindingRecord(
                id="rb_other_admin1234567",
                organization_id="org_1234567890abcdef",
                workspace_id=None,
                principal_type="user",
                principal_id=other_id,
                resource_type="organization",
                resource_id="org_1234567890abcdef",
                role_key="admin",
                created_by_user_id=other_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with pytest.raises(PluginError) as hidden:
        await coordinator.get_receipt(
            actor=actor(other_id),
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            operation_id=accepted.operation_id,
        )
    assert hidden.value.code == "plugin_operation_not_found"


@pytest.mark.anyio
async def test_expired_executor_lease_is_reclaimed_with_new_generation(
    runner_plugin_service: PluginService,
    runtime_coordinator: tuple[PluginRuntimeCommandCoordinator, _CandidateResolver, _StagingAuthority],
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    coordinator, _resolver, _authority = runtime_coordinator
    _uploaded, accepted = await _activate(
        runner_plugin_service,
        coordinator,
        version="1.0.0",
        key="recover-expired-lease",
    )
    async with transaction(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
        assert state is not None
        state.command_operation_id = accepted.operation_id
        state.command_claim_generation = 7
        state.command_lease_expires_at = NOW - timedelta(seconds=1)

    assert await coordinator.reconcile_once() is True
    receipt = await coordinator.get_receipt(
        actor=actor(),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        operation_id=accepted.operation_id,
    )
    assert receipt.status == "succeeded"
    async with short_session(plugin_sessions) as session:
        state = await session.get(PluginRuntimeStateRecord, "runtime")
        assert state is not None and state.command_claim_generation == 8
