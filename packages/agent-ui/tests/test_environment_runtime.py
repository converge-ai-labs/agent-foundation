from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_harness import __version__ as harness_version
from a13n_ui.composition import (
    AgentCompositionResolver,
    CompositionAcceptanceService,
)
from a13n_ui.composition.models import ResolvedEnvironmentProfile
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.environment_runtime import (
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    normalize_workspace_binding,
)
from a13n_ui.errors import CompositionError, EnvironmentLifecycleError
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import (
    ObjectKind,
    SnapshotRef,
    StoredSessionContinuation,
    open_local_store,
)
from anyio import fail_after, sleep_forever

pytestmark = pytest.mark.anyio


async def _accepted_native_session(tmp_path: Path):
    source_path = tmp_path / "agent-ui.yaml"
    source_path.write_text(
        """
schema_version: "1"
defaults:
  agent: assistant
  environment: native
models:
  primary:
    model: openai:gpt-5
agents:
  assistant:
    model: primary
environments:
  native:
    kind: native
""".lstrip()
    )
    source = await load_agent_ui_configuration(source_path)
    context = open_local_store(StorageSettings(data_root=tmp_path / "state"))
    store = await context.__aenter__()
    accepted = await CompositionAcceptanceService(store, AgentCompositionResolver()).accept(
        source,
        expected_current_digest=None,
    )
    root_state = HarnessState.new()
    continuation = await store.objects.publish_model(
        object_kind=ObjectKind.session_continuation,
        value=StoredSessionContinuation(
            harness_release=harness_version,
            harness_state=root_state,
            created_at=datetime.now(UTC),
        ),
    )
    session = await store.sessions.create(
        session_id="session-environment",
        root_thread_id=root_state.thread_id,
        agent_snapshot=SnapshotRef(
            snapshot_kind="agent",
            object=accepted.snapshots[("agent", "assistant")],
        ),
        environment_snapshot=SnapshotRef(
            snapshot_kind="environment",
            object=accepted.snapshots[("environment", "native")],
        ),
        continuation=continuation.ref,
        created_at=datetime.now(UTC),
    )
    return context, store, session


async def test_workspace_binding_normalizes_and_preserves_first_occurrence(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    binding = await normalize_workspace_binding((first, first / ".", second))

    assert binding.folders == (first.resolve(), second.resolve())


async def test_workspace_binding_rejects_missing_and_non_directory_paths(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory")

    with pytest.raises(EnvironmentLifecycleError) as empty:
        await normalize_workspace_binding(())
    assert empty.value.code == "workspace_binding_empty"

    with pytest.raises(EnvironmentLifecycleError) as invalid:
        await normalize_workspace_binding((file_path,))
    assert invalid.value.code == "workspace_folder_invalid"


async def test_native_profile_prepares_deterministic_fresh_mounts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    second = tmp_path / "second"
    workspace.mkdir()
    second.mkdir()
    context, store, session = await _accepted_native_session(tmp_path)
    try:
        binding = await normalize_workspace_binding((workspace, second))
        reconstructor = EnvironmentSnapshotReconstructor(
            local_runtime_parent=store.layout.runtimes,
        )

        plan = await EnvironmentRunService(store, reconstructor).prepare(session, binding)

        assert tuple(plan.environments) == ("workspace", "workspace-2")
        assert plan.default_environment == "workspace"
        assert all(item.provider_key == "a13n.direct-local" for item in plan.environments.values())
        finalization = await plan.finalize()
        assert finalization.cleanup_errors == ()
        assert tuple(item.status for item in finalization.state_publications) == (
            "unchanged",
            "unchanged",
        )
        heads = [await store.environment_states.get(item.key) for item in finalization.state_publications]
        assert all(head is not None and head.state is None for head in heads)
    finally:
        await context.__aexit__(None, None, None)


async def test_environment_finalization_bounds_a_hung_adapter_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context, store, session = await _accepted_native_session(tmp_path)
    try:
        binding = await normalize_workspace_binding((workspace,))
        plan = await EnvironmentRunService(
            store,
            EnvironmentSnapshotReconstructor(local_runtime_parent=store.layout.runtimes),
        ).prepare(session, binding)
        environment = plan.environments["workspace"]

        async def stalled_close() -> None:
            await sleep_forever()

        monkeypatch.setattr(environment, "close", stalled_close)
        with fail_after(1):
            finalization = await plan.finalize(timeout_seconds=0.01)

        assert len(finalization.cleanup_errors) == 1
        assert isinstance(finalization.cleanup_errors[0], EnvironmentLifecycleError)
        assert finalization.cleanup_errors[0].code == "environment_cleanup_timeout"
    finally:
        await context.__aexit__(None, None, None)


async def test_environment_reconstruction_rejects_pinned_provenance_change(tmp_path: Path) -> None:
    context, store, session = await _accepted_native_session(tmp_path)
    try:
        profile = await store.objects.read_model(
            session.environment_snapshot.object,
            ResolvedEnvironmentProfile,
        )
        changed = profile.model_copy(
            update={"binder_lock": profile.binder_lock.model_copy(update={"distribution_version": "incompatible"})}
        )

        with pytest.raises(CompositionError) as mismatch:
            EnvironmentSnapshotReconstructor().reconstruct(changed)

        assert mismatch.value.code == "environment_snapshot_provenance_mismatch"
    finally:
        await context.__aexit__(None, None, None)


async def test_local_eip_requires_explicit_or_managed_runtime(tmp_path: Path) -> None:
    source_path = tmp_path / "agent-ui.yaml"
    source_path.write_text(
        """
schema_version: "1"
defaults:
  agent: assistant
  environment: sandbox
models:
  primary:
    model: openai:gpt-5
agents:
  assistant:
    model: primary
environments:
  sandbox:
    kind: local_eip
""".lstrip()
    )
    source = await load_agent_ui_configuration(source_path)
    context = open_local_store(StorageSettings(data_root=tmp_path / "state"))
    store = await context.__aenter__()
    try:
        accepted = await CompositionAcceptanceService(store, AgentCompositionResolver()).accept(
            source,
            expected_current_digest=None,
        )
        state = HarnessState.new()
        continuation = await store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=StoredSessionContinuation(
                harness_release=harness_version,
                harness_state=state,
                created_at=datetime.now(UTC),
            ),
        )
        session = await store.sessions.create(
            session_id="session-local-eip",
            root_thread_id=state.thread_id,
            agent_snapshot=SnapshotRef(
                snapshot_kind="agent",
                object=accepted.snapshots[("agent", "assistant")],
            ),
            environment_snapshot=SnapshotRef(
                snapshot_kind="environment",
                object=accepted.snapshots[("environment", "sandbox")],
            ),
            continuation=continuation.ref,
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        binding = await normalize_workspace_binding((workspace,))

        with pytest.raises(EnvironmentLifecycleError) as unavailable:
            await EnvironmentRunService(
                store,
                EnvironmentSnapshotReconstructor(),
            ).prepare(session, binding)

        assert unavailable.value.code == "local_eip_runtime_unavailable"
    finally:
        await context.__aexit__(None, None, None)
