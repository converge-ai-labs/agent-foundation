from __future__ import annotations

from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentAttachment,
    DirectLocalEnvironmentManager,
    DirectLocalProviderConfiguration,
    DirectLocalProviderRuntime,
    DirectLocalRootConfiguration,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderError,
    EnvironmentProviderResourceState,
    EnvironmentProviderSpec,
    EnvironmentReconciliationPhase,
    build_environment_provider_factory_catalog,
)

pytestmark = pytest.mark.anyio


def _operation(action: EnvironmentManagementAction, suffix: str) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{suffix}",
        action=action,
        resource_correlation="resource-local-1",
        attempt=1,
    )


def _manager(root: Path, *, environment_id: str = "local-1") -> DirectLocalEnvironmentManager:
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))
    manager = catalog.create_manager(
        spec=EnvironmentProviderSpec(
            provider_key="a13n.direct-local",
            schema_version="1",
            parameters={
                "environment_id": environment_id,
                "root": {"path": str(root)},
            },
        ),
        runtime=DirectLocalProviderRuntime(),
    )
    assert isinstance(manager, DirectLocalEnvironmentManager)
    return manager


async def test_direct_local_create_issues_shared_fresh_attachments_without_mutating_root(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    manager = _manager(tmp_path)
    resource = await manager.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        async with (
            resource.acquire_attachment() as first,
            resource.acquire_attachment() as second,
        ):
            assert isinstance(first, DirectLocalEnvironmentAttachment)
            assert isinstance(second, DirectLocalEnvironmentAttachment)
            assert first.attachment_id != second.attachment_id
            assert first.configuration.root.path == tmp_path.resolve()
        state = resource.state

    await manager.destroy(
        state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )
    assert marker.read_text() == "preserve"
    assert tuple(tmp_path.iterdir()) == (marker,)


async def test_direct_local_resume_validates_exact_state_and_existing_directory(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    created = await manager.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    state = created.state

    resumed = await manager.resume(
        state,
        operation=_operation(EnvironmentManagementAction.RESUME, "resume"),
    )
    async with resumed:
        async with resumed.acquire_attachment() as attachment:
            assert attachment.environment_id == "local-1"

    invalid = EnvironmentProviderResourceState(
        provider_key=state.provider_key,
        state_version=state.state_version,
        data={
            "environment_id": "other",
            "configuration_fingerprint": "sha256:" + "0" * 64,
        },
    )
    with pytest.raises(EnvironmentProviderError) as exc_info:
        await manager.resume(
            invalid,
            operation=_operation(EnvironmentManagementAction.RESUME, "invalid"),
        )
    assert exc_info.value.code == "provider_state_invalid"


async def test_direct_local_pause_is_rejected_before_effect(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    resource = await manager.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    async with resource:
        with pytest.raises(EnvironmentProviderError) as exc_info:
            await manager.pause(
                resource,
                operation=_operation(EnvironmentManagementAction.PAUSE, "pause"),
                mode=EnvironmentPauseMode.FILESYSTEM,
            )
    assert exc_info.value.code == "provider_action_unsupported"
    assert tmp_path.is_dir()


async def test_direct_local_reconciliation_is_deterministic_and_read_only(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    create_operation = _operation(EnvironmentManagementAction.CREATE, "create")
    running = await manager.reconcile(create_operation, last_known_state=None)
    assert running.phase is EnvironmentReconciliationPhase.RUNNING
    assert running.state is not None

    tmp_path.rmdir()
    absent = await manager.reconcile(create_operation, last_known_state=None)
    assert absent.phase is EnvironmentReconciliationPhase.ABSENT
    assert absent.state is None

    destroyed = await manager.reconcile(
        _operation(EnvironmentManagementAction.DESTROY, "destroy"),
        last_known_state=running.state,
    )
    assert destroyed.phase is EnvironmentReconciliationPhase.ABSENT


def test_direct_local_configuration_requires_existing_path_shape_but_not_existence(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    configuration = DirectLocalProviderConfiguration(
        environment_id="local-1",
        root=DirectLocalRootConfiguration(path=missing),
    )
    assert configuration.root.path == missing


async def test_direct_local_state_uses_canonical_root_and_rejects_symlink_retarget(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(first_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    direct = await _manager(first_root).create(operation=_operation(EnvironmentManagementAction.CREATE, "direct"))
    via_alias_manager = _manager(alias)
    via_alias = await via_alias_manager.create(operation=_operation(EnvironmentManagementAction.CREATE, "alias"))
    assert direct.state == via_alias.state

    alias.unlink()
    alias.symlink_to(second_root, target_is_directory=True)
    with pytest.raises(EnvironmentProviderError) as exc_info:
        await via_alias_manager.resume(
            via_alias.state,
            operation=_operation(EnvironmentManagementAction.RESUME, "retargeted"),
        )
    assert exc_info.value.code == "provider_state_invalid"


async def test_direct_local_destroy_rejects_another_environment_state(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _manager(first_root, environment_id="local-1")
    created = await first.create(operation=_operation(EnvironmentManagementAction.CREATE, "first"))

    for index, second in enumerate(
        (
            _manager(first_root, environment_id="local-2"),
            _manager(second_root, environment_id="local-1"),
        ),
        start=1,
    ):
        with pytest.raises(EnvironmentProviderError) as exc_info:
            await second.destroy(
                created.state,
                operation=_operation(EnvironmentManagementAction.DESTROY, f"second-{index}"),
            )
        assert exc_info.value.code == "provider_state_invalid"


async def test_direct_local_rejects_inconsistent_operation_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    operation = _operation(EnvironmentManagementAction.CREATE, "shared")
    resource = await manager.create(operation=operation)

    with pytest.raises(EnvironmentProviderError) as reuse_error:
        await manager.destroy(
            resource.state,
            operation=operation.model_copy(
                update={
                    "action": EnvironmentManagementAction.DESTROY,
                    "resource_correlation": "resource-other",
                }
            ),
        )
    assert reuse_error.value.code == "provider_conflict"

    with pytest.raises(EnvironmentProviderError) as reconcile_error:
        await manager.reconcile(
            operation.model_copy(
                update={
                    "action": EnvironmentManagementAction.DESTROY,
                    "resource_correlation": "resource-other",
                }
            ),
            last_known_state=resource.state,
        )
    assert reconcile_error.value.code == "provider_conflict"


def test_direct_local_configuration_rejects_nul_in_os_bound_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DirectLocalRootConfiguration(path=Path(f"{tmp_path}\x00other"))
    with pytest.raises(ValueError):
        DirectLocalProviderConfiguration(
            environment_id="local-1",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({Path("/bin/tool\x00other")}),
        )
