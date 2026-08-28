from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

import a13n_harness as harness_module
import a13n_harness.environment as environment_module
import a13n_harness.environment.local as local_module
import a13n_harness.environment.local.binding as local_binding_module
import a13n_harness.environment.local.files as local_files_module
import a13n_harness.environment.local.processes as local_processes_module
import a13n_harness.environment.local.retention as local_retention_module
import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalProviderRuntime,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    FileQueryRequest,
    FileTextSearchRequest,
    OpaqueOutputReference,
)
from a13n_harness.environment.advanced import (
    EnvironmentBindingRequest,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    create_environment_provider_binding,
    create_environment_run_binding,
)
from a13n_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema
from pydantic_core import PydanticSerializationError

pytestmark = pytest.mark.anyio
_PROCESS_EXECUTABLE = Path(sys.executable).resolve()


def _write_utf8(path: Path, value: str) -> None:
    path.write_bytes(value.encode("utf-8"))


def _read_utf8(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-1",
    )


def test_direct_local_internal_facets_are_not_public_exports() -> None:
    for module in (harness_module, environment_module, local_module):
        assert "LocalFileOperator" not in module.__all__
        assert "LocalShell" not in module.__all__
        assert not hasattr(module, "LocalFileOperator")
        assert not hasattr(module, "LocalShell")


def test_direct_local_configuration_defaults_and_exact_schema(tmp_path: Path) -> None:
    configuration = DirectLocalProviderConfiguration(
        environment_id="local-defaults",
        root=DirectLocalRootConfiguration(path=tmp_path),
    )
    assert configuration.max_value_bytes == 16 * 1024 * 1024
    assert configuration.max_concurrent_processes == 128
    assert configuration.max_wall_time_seconds == 24 * 60 * 60
    assert configuration.terminate_grace_seconds == 5
    assert configuration.max_buffer_bytes == 1024 * 1024
    assert configuration.max_spool_bytes == 64 * 1024 * 1024 * 1024

    base = {
        "environment_id": "invalid-policy",
        "root": DirectLocalRootConfiguration(path=tmp_path),
    }
    for extra in (
        {"max_file_bytes": 64 * 1024 * 1024},
        {"max_stdin_bytes": 64 * 1024 * 1024},
        {"retention_seconds": 60},
        {"ports_enabled": True},
    ):
        with pytest.raises(ValidationError):
            DirectLocalProviderConfiguration(**base, **extra)
    with pytest.raises(ValidationError):
        DirectLocalShellProfile(profile_id="relative", executable=Path("bin/sh"))
    with pytest.raises(ValidationError):
        DirectLocalShellProfile(profile_id="", executable=_PROCESS_EXECUTABLE)


async def test_host_manager_attachment_path_supports_sequential_harness_runs(tmp_path: Path) -> None:
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))
    manager = catalog.create_provider(
        EnvironmentProviderSpec(
            provider_key="a13n.direct-local",
            schema_version="1",
            parameters={
                "environment_id": "host-managed-local",
                "root": {"path": str(tmp_path)},
            },
        ),
        runtime=DirectLocalProviderRuntime(),
    )

    for attempt in (1, 2):
        managed = await manager.create(
            operation=EnvironmentOperationContext(
                operation_id=f"operation-create-{attempt}",
                action=EnvironmentManagementAction.CREATE,
                resource_correlation="resource-host-managed-local",
                attempt=1,
            )
        )
        state = managed.state
        async with managed:
            async with managed.acquire_attachment() as attachment:
                provider = create_environment_provider_binding(attachment)
                request = EnvironmentTopologyRequest(
                    topology_version=1,
                    bindings=(
                        EnvironmentBindingRequest(
                            binding_id="binding-1",
                            binding_revision=1,
                            alias="local",
                            permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                            default_working_directory="/",
                            provider_binding=provider,
                        ),
                    ),
                    default_binding_id="binding-1",
                )
                binding = create_environment_run_binding(
                    initial_topology=request,
                    topology_limits=EnvironmentTopologyLimits(),
                    state_limits=EnvironmentStateLimits(),
                )
                async with binding.bind(run_id=f"run-{attempt}", instance=_instance()) as environment:
                    if attempt == 1:
                        await environment.files.write_text("/workspace/shared.txt", "preserved", mode="create")
                    else:
                        assert (await environment.files.read_text("/workspace/shared.txt")).text == "preserved"
        await manager.destroy(
            state,
            operation=EnvironmentOperationContext(
                operation_id=f"operation-destroy-{attempt}",
                action=EnvironmentManagementAction.DESTROY,
                resource_correlation="resource-host-managed-local",
                attempt=1,
            ),
        )

    assert (tmp_path / "shared.txt").read_text() == "preserved"


def _two_binding_aggregate(source: Path, destination: Path):
    providers = (
        DirectLocalEnvironmentProviderBinding(
            DirectLocalProviderConfiguration(
                environment_id="local-source",
                root=DirectLocalRootConfiguration(path=source),
            )
        ),
        DirectLocalEnvironmentProviderBinding(
            DirectLocalProviderConfiguration(
                environment_id="local-destination",
                root=DirectLocalRootConfiguration(path=destination),
            )
        ),
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=tuple(
            EnvironmentBindingRequest(
                binding_id=f"binding-{alias}",
                binding_revision=1,
                alias=alias,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            )
            for alias, provider in zip(("source", "destination"), providers, strict=True)
        ),
        default_binding_id="binding-source",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _aggregate(
    root: Path,
    *,
    read_only: bool = False,
    max_value_bytes: int = 16 * 1024 * 1024,
):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-1",
            root=DirectLocalRootConfiguration(
                path=root,
                read_only=read_only,
            ),
            max_value_bytes=max_value_bytes,
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_direct_local_text_patch_copy_and_routing(tmp_path: Path) -> None:
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        written = await environment.files.write_text(
            "/workspace/note.txt",
            "alpha\nbeta\n",
            mode="create",
        )
        assert written.bytes_written == len(b"alpha\nbeta\n")
        observed = await environment.files.read_text("note.txt")
        assert observed.text == "alpha\nbeta\n"
        assert observed.path == "note.txt"

        patched = await environment.files.patch_text(
            "/workspace/note.txt",
            "@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n",
        )
        assert patched.hunks_applied == 1
        copied = await environment.files.copy(
            "/workspace/note.txt",
            "/workspace/copied.txt",
        )
        assert copied.bytes_copied == len(b"alpha\ngamma\n")
        assert _read_utf8(tmp_path / "copied.txt") == "alpha\ngamma\n"


async def test_exact_dot_selects_the_default_working_directory_without_allowing_traversal(tmp_path: Path) -> None:
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        selected = environment.resolve_path(".")
        assert selected.binding_id == "binding-1"
        assert selected.path == "/"

        for invalid in ("./note.txt", "..", "nested/../note.txt"):
            with pytest.raises(EnvironmentError) as exc_info:
                environment.resolve_path(invalid)
            assert exc_info.value.code == "environment_request_invalid"


@pytest.mark.parametrize(
    ("source", "patch", "expected"),
    [
        ("a\rb\n", "@@ -1 +1 @@\n-a\rb\n+x\n", "x\n"),
        ("a\u2028b\n", "@@ -1 +1 @@\n-a\u2028b\n+x\n", "x\n"),
        (
            "tail",
            "@@ -1 +1 @@\n-tail\n\\ No newline at end of file\n+done\n\\ No newline at end of file",
            "done",
        ),
    ],
)
async def test_direct_local_patch_uses_lf_only_lines_without_normalizing_content(
    tmp_path: Path,
    source: str,
    patch: str,
    expected: str,
) -> None:
    _write_utf8(tmp_path / "value.txt", source)
    binding = _aggregate(tmp_path)

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.files.patch_text("/workspace/value.txt", patch)

    assert result.hunks_applied == 1
    assert _read_utf8(tmp_path / "value.txt") == expected


async def test_direct_local_rejects_escape_symlink_and_read_only_mutation(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-environment.txt"
    _write_utf8(outside, "secret")
    (tmp_path / "link").symlink_to(outside)

    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError):
            await environment.files.read_text("/workspace/../outside-environment.txt")
        with pytest.raises(EnvironmentError) as escaped:
            await environment.files.read_text("/workspace/link")
        assert escaped.value.code == "environment_denied"

    read_only = _aggregate(tmp_path, read_only=True)
    async with read_only.bind(run_id="run-2", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied:
            await environment.files.write_text("/workspace/new.txt", "x", mode="create")
        assert denied.value.code == "environment_denied"


async def test_file_only_binding_does_not_advertise_or_create_output_operations(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-files-only",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        assert entered.operations.outputs is None
        assert "outputs" not in entered.descriptor.operation_families
        assert EnvironmentAction.OUTPUT_READ not in entered.descriptor.permissions.operations


async def test_read_only_binding_advertises_only_effective_file_permissions(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="read-only-permissions",
            root=DirectLocalRootConfiguration(
                path=tmp_path,
                read_only=True,
            ),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        permissions = entered.descriptor.permissions.operations
        assert EnvironmentAction.FILE_READ_TEXT in permissions
        assert EnvironmentAction.FILE_SEARCH_TEXT in permissions
        assert EnvironmentAction.FILE_WRITE_TEXT not in permissions
        assert EnvironmentAction.FILE_MKDIR not in permissions
        assert EnvironmentAction.FILE_REMOVE not in permissions


async def test_direct_local_read_race_returns_environment_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "race.bin"
    target.write_bytes(b"value")
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="read-race",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )
    original = local_files_module._read_bytes_at_most

    def remove_before_read(path: Path, offset: int, length: int | None, max_bytes: int) -> bytes:
        path.unlink()
        return original(path, offset, length, max_bytes)

    monkeypatch.setattr(local_files_module, "_read_bytes_at_most", remove_before_read)
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        files = entered.operations.files
        assert files is not None
        with pytest.raises(EnvironmentError) as missing:
            await files.read_bytes("/race.bin")
        assert missing.value.code == "environment_not_found"


async def test_write_destination_inspection_errors_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "blocked.txt"
    original_lstat = Path.lstat

    def deny_target_lstat(path: Path) -> object:
        if path == target:
            raise PermissionError(13, "permission denied", str(target))
        return original_lstat(path)

    binding = _aggregate(tmp_path)
    monkeypatch.setattr(Path, "lstat", deny_target_lstat)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied:
            await environment.files.write_text(
                "/workspace/blocked.txt",
                "value",
                mode="create",
            )
        assert denied.value.code == "environment_denied"

    assert not target.exists()
    assert not tuple(tmp_path.glob(".a13n-write-*"))


async def test_cancelled_spool_allocation_is_joined_and_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()
    spool = tmp_path / "spool"

    def blocked_mkdtemp(*, prefix: str) -> str:
        assert prefix == "a13n-output-"
        started.set()
        release.wait(timeout=5)
        spool.mkdir()
        return str(spool)

    monkeypatch.setattr(local_binding_module.tempfile, "mkdtemp", blocked_mkdtemp)
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-cancelled-spool",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
        )
    )
    scope = provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    )
    entering = asyncio.create_task(scope.__aenter__())
    assert await asyncio.to_thread(started.wait, 5)
    entering.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await entering

    assert not spool.exists()


async def test_binding_teardown_attempts_spool_cleanup_and_preserves_shared_root_after_process_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared-cleanup"
    shared.mkdir()
    retention_closed = False
    original_retention_close = local_retention_module.LocalRetentionStore.close

    async def fail_process_close(manager) -> None:
        raise RuntimeError("forced process cleanup failure")

    async def observe_retention_close(store) -> None:
        nonlocal retention_closed
        retention_closed = True
        await original_retention_close(store)

    monkeypatch.setattr(local_processes_module.LocalProcessManager, "close", fail_process_close)
    monkeypatch.setattr(local_retention_module.LocalRetentionStore, "close", observe_retention_close)
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-cleanup-failure",
            root=DirectLocalRootConfiguration(path=shared),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
        )
    )

    with pytest.raises(RuntimeError, match="forced process cleanup failure"):
        async with provider.bind(
            run_id="run-1",
            instance=_instance(),
            binding_id="binding-1",
            binding_revision=1,
        ):
            pass

    assert retention_closed is True
    assert shared.exists()


async def test_direct_local_requires_and_preserves_shared_root(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-shared",
            root=DirectLocalRootConfiguration(path=shared),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-shared",
                binding_revision=1,
                alias="shared",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-shared",
    )
    binding = create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.files.write_text("/workspace/value.txt", "value", mode="create")
    assert (shared / "value.txt").read_text() == "value"


async def test_raw_reads_are_at_most_and_stream_writes_publish_only_on_success(tmp_path: Path) -> None:
    (tmp_path / "large.bin").write_bytes(b"12345")
    binding = _aggregate(
        tmp_path,
        max_value_bytes=4,
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as too_large:
            await environment.files.read_bytes("/workspace/large.bin")
        assert too_large.value.code == "environment_too_large"

        assert (
            await environment.files.read_bytes(
                "/workspace/large.bin",
                offset=1,
                length=4,
            )
            == b"2345"
        )
        assert (
            await environment.files.read_bytes(
                "/workspace/large.bin",
                offset=100,
                length=100,
            )
            == b""
        )
        assert (
            await environment.files.read_bytes(
                "/workspace/large.bin",
                length=0,
            )
            == b""
        )

        shrinking = tmp_path / "shrinking.bin"
        shrinking.write_bytes(b"12")
        assert (
            await environment.files.read_bytes(
                "/workspace/shrinking.bin",
                length=100,
            )
            == b"12"
        )

        appended = await environment.files.write_text(
            "/workspace/shrinking.bin",
            "3",
            mode="append",
        )
        assert appended.bytes_written == 1
        assert shrinking.read_bytes() == b"123"

        patch_target = tmp_path / "patch-target.txt"
        _write_utf8(patch_target, "a\n")
        with pytest.raises(EnvironmentError) as patch_too_large:
            await environment.files.patch_text(
                "/workspace/patch-target.txt",
                "@@ -1 +1 @@\n-a\n+abcde\n",
            )
        assert patch_too_large.value.code == "environment_too_large"
        assert _read_utf8(patch_target) == "a\n"

        with pytest.raises(EnvironmentError) as missing_append:
            await environment.files.write_text(
                "/workspace/missing.txt",
                "x",
                mode="append",
            )
        assert missing_append.value.code == "environment_not_found"
        assert not (tmp_path / "missing.txt").exists()

        (tmp_path / "append-directory").mkdir()
        with pytest.raises(EnvironmentError) as directory_append:
            await environment.files.write_text(
                "/workspace/append-directory",
                "x",
                mode="append",
            )
        assert directory_append.value.code == "environment_request_invalid"

        invalid_append = tmp_path / "invalid-append.bin"
        invalid_append.write_bytes(b"\xff")
        with pytest.raises(EnvironmentError) as unsupported_append:
            await environment.files.write_text(
                "/workspace/invalid-append.bin",
                "x",
                mode="append",
            )
        assert unsupported_append.value.code == "environment_unsupported"
        assert invalid_append.read_bytes() == b"\xff"

        async def interrupted_stream():
            yield b"data"
            raise RuntimeError("source failed")

        with pytest.raises(RuntimeError, match="source failed"):
            await environment.files.write_bytes_stream(
                "/workspace/aborted.bin",
                interrupted_stream(),
                mode="create",
            )
        assert not (tmp_path / "aborted.bin").exists()
        assert not tuple(tmp_path.glob(".a13n-write-*"))

        streamed = b"".join([chunk async for chunk in environment.files.read_bytes_stream("/workspace/large.bin")])
        assert streamed == b"12345"

        appended_large = await environment.files.write_text("/workspace/large.bin", "x", mode="append")
        assert appended_large.bytes_written == 1
        assert (tmp_path / "large.bin").read_bytes() == b"12345x"

        (tmp_path / "unbounded-stream.bin").write_bytes(b"123456789")
        unbounded = b"".join(
            [chunk async for chunk in environment.files.read_bytes_stream("/workspace/unbounded-stream.bin")]
        )
        assert unbounded == b"123456789"

        async def larger_than_value_limit():
            yield b"1234"
            yield b"56789"

        streamed_write = await environment.files.write_bytes_stream(
            "/workspace/streamed.bin",
            larger_than_value_limit(),
            mode="create",
        )
        assert streamed_write.bytes_written == 9
        assert (tmp_path / "streamed.bin").read_bytes() == b"123456789"
        assert not tuple(tmp_path.glob(".a13n-write-*"))


async def test_create_publish_succeeds_when_staging_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink
    cleanup_failed = False

    def fail_staging_cleanup_once(path: Path, *args, **kwargs) -> None:
        nonlocal cleanup_failed
        if path.name.startswith(".a13n-write-") and not cleanup_failed:
            cleanup_failed = True
            raise OSError("injected staging cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staging_cleanup_once)
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.files.write_text(
            "/workspace/published.txt",
            "published",
            mode="create",
        )

    assert cleanup_failed
    assert result.receipt.outcome == "succeeded"
    assert (tmp_path / "published.txt").read_bytes() == b"published"


async def test_file_publication_returns_committed_outcome_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_started = threading.Event()
    continue_publication = threading.Event()
    original_publish = local_files_module.LocalFileOperator._publish_staged

    def blocking_publish(operator, staged, destination, mode) -> None:
        publication_started.set()
        assert continue_publication.wait(timeout=1)
        original_publish(operator, staged, destination, mode)

    monkeypatch.setattr(local_files_module.LocalFileOperator, "_publish_staged", blocking_publish)
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        write = asyncio.create_task(
            environment.files.write_text(
                "/workspace/published.txt",
                "published",
                mode="create",
            )
        )
        assert await asyncio.to_thread(publication_started.wait, 1)
        write.cancel()
        continue_publication.set()
        result = await asyncio.wait_for(write, timeout=0.5)

    assert result.receipt.outcome == "succeeded"
    assert (tmp_path / "published.txt").read_bytes() == b"published"
    assert not tuple(tmp_path.glob(".a13n-write-*"))


@pytest.mark.parametrize("cross_binding", [False, True])
async def test_copy_accepts_source_eof_without_completion_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cross_binding: bool,
) -> None:
    if cross_binding:
        source_root = tmp_path / "source"
        destination_root = tmp_path / "destination"
        source_root.mkdir()
        destination_root.mkdir()
        binding = _two_binding_aggregate(source_root, destination_root)
        source = source_root / "value.bin"
        destination = destination_root / "copied.bin"
        destination_path = "/environment/destination/copied.bin"
    else:
        source_root = tmp_path
        binding = _aggregate(source_root)
        source = source_root / "value.bin"
        destination = source_root / "copied.bin"
        destination_path = "/workspace/copied.bin"
    source.write_bytes(b"abcdefgh")
    original = local_files_module.LocalFileOperator.read_bytes_stream

    async def shrink_then_read(operator, path, *, chunk_size=65_536):
        if operator._root == source_root.resolve() and path == "/value.bin":
            source.write_bytes(b"abc")
        async for chunk in original(operator, path, chunk_size=chunk_size):
            yield chunk

    monkeypatch.setattr(local_files_module.LocalFileOperator, "read_bytes_stream", shrink_then_read)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        copied = await environment.files.copy("/workspace/value.bin", destination_path)
    assert copied.bytes_copied == 3
    assert destination.read_bytes() == b"abc"
    assert not tuple(destination.parent.glob(".a13n-write-*"))


async def test_text_read_uses_zero_based_line_offsets_without_splitting_utf8_lines(tmp_path: Path) -> None:
    content = "skip\naaaaaétail\nlast\n"
    _write_utf8(tmp_path / "value.txt", content)
    binding = _aggregate(tmp_path)

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        first = await environment.files.read_text(
            "/workspace/value.txt",
            line_offset=1,
            line_limit=1,
        )
        assert first.text == "aaaaaétail\n"
        assert first.line_offset == 1
        assert first.lines_read == 1
        assert first.has_more is True
        assert first.truncated_lines == ()

        truncated = await environment.files.read_text(
            "/workspace/value.txt",
            line_offset=1,
            line_limit=1,
            max_line_length=5,
        )
        assert truncated.text == "aaaaa\n"
        assert truncated.truncated_lines == (2,)
        assert truncated.has_more is True

        second = await environment.files.read_text(
            "/workspace/value.txt",
            line_offset=first.line_offset + first.lines_read,
            line_limit=1,
        )
        assert second.text == "last\n"
        assert second.line_offset == 2
        assert second.lines_read == 1
        assert second.has_more is False

        exhausted = await environment.files.read_text(
            "/workspace/value.txt",
            line_offset=3,
            line_limit=1,
        )
        assert exhausted.text == ""
        assert exhausted.lines_read == 0
        assert exhausted.has_more is False

        (tmp_path / "invalid-after-page.txt").write_bytes(b"valid\n\xff")
        valid_page = await environment.files.read_text(
            "/workspace/invalid-after-page.txt",
            line_limit=1,
        )
        assert valid_page.text == "valid\n"
        assert valid_page.lines_read == 1
        assert valid_page.has_more is True
        with pytest.raises(EnvironmentError) as invalid_page:
            await environment.files.read_text(
                "/workspace/invalid-after-page.txt",
                line_offset=1,
                line_limit=1,
            )
        assert invalid_page.value.code == "environment_unsupported"
        with pytest.raises(EnvironmentError) as invalid_patch:
            await environment.files.patch_text(
                "/workspace/invalid-after-page.txt",
                "@@ -1 +1 @@\n-valid\n+updated\n",
            )
        assert invalid_patch.value.code == "environment_unsupported"

        boundaries = tmp_path / "boundaries.txt"
        _write_utf8(boundaries, "a\rb\nc\u2028d\r\ne")
        selected = await environment.files.read_text(
            "/workspace/boundaries.txt",
            line_offset=1,
            line_limit=1,
        )
        assert selected.text == "c\u2028d\r\n"
        assert selected.lines_read == 1
        assert selected.has_more is True
        searched = await environment.files.search_text(
            FileTextSearchRequest(
                root="/workspace",
                pattern="d",
                max_matches=10,
            )
        )
        match = next(match for match in searched.matches if match.path.endswith("boundaries.txt"))
        assert match.line == 2
        assert match.text == "c\u2028d\r"
        assert match.text_truncated is False


async def test_query_is_deterministic_bounded_and_offset_continues(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    _write_utf8(tmp_path / "a" / "1.txt", "1")
    _write_utf8(tmp_path / "a" / "2.txt", "2")
    _write_utf8(tmp_path / "b.txt", "b")
    _write_utf8(tmp_path / ".hidden.txt", "hidden")
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        request = FileQueryRequest(
            root="/workspace",
            pattern="*",
            recursive=True,
            include_hidden=False,
            kinds=frozenset({"file"}),
            max_results=2,
        )
        first = await environment.files.query(request)
        assert [entry.path for entry in first.entries] == [
            "/workspace/a/1.txt",
            "/workspace/a/2.txt",
        ]
        assert first.offset == 0
        assert first.has_more is True

        second = await environment.files.query(request.model_copy(update={"offset": first.offset + len(first.entries)}))
        assert [entry.path for entry in second.entries] == ["/workspace/b.txt"]
        assert second.offset == 2
        assert second.has_more is False

        deep_glob = "/".join(["**"] * 1_200 + ["*.txt"])
        deep_result = await environment.files.query(
            request.model_copy(update={"pattern": deep_glob, "max_results": 10})
        )
        assert [entry.path for entry in deep_result.entries] == [
            "/workspace/a/1.txt",
            "/workspace/a/2.txt",
            "/workspace/b.txt",
        ]
        assert deep_result.has_more is False

        with pytest.raises(EnvironmentError) as invalid_pattern:
            await environment.files.query(request.model_copy(update={"pattern": "x" * (16 * 1024 + 1)}))
        assert invalid_pattern.value.code == "environment_request_invalid"


async def test_search_iterates_lines_without_materializing_a_second_line_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_utf8(tmp_path / "many-lines.txt", "\n" * 10_000 + "needle\n")
    monkeypatch.setattr(
        local_files_module,
        "_split_lf_lines",
        lambda _: pytest.fail("search must not materialize patch line collections"),
    )
    binding = _aggregate(tmp_path)

    async with binding.bind(run_id="run-search-lines", instance=_instance()) as environment:
        result = await environment.files.search_text(
            FileTextSearchRequest(root="/workspace", pattern="needle", max_matches=10)
        )

    assert len(result.matches) == 1
    assert result.matches[0].line == 10_001


async def test_search_skips_nul_files_and_streams_files_larger_than_value_limit(tmp_path: Path) -> None:
    nul_file = tmp_path / "nul.txt"
    nul_file.write_bytes(b"needle\x00hidden\n")
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-nul", instance=_instance()) as environment:
        result = await environment.files.search_text(
            FileTextSearchRequest(
                root="/workspace",
                pattern="needle",
                max_matches=10,
            )
        )
        assert result.matches == ()
        assert result.has_more is False

    nul_file.unlink()
    _write_utf8(tmp_path / "large.txt", "haystack\n" * 10 + "needle\n")
    limited = _aggregate(tmp_path, max_value_bytes=16)
    async with limited.bind(run_id="run-large", instance=_instance()) as environment:
        result = await environment.files.search_text(
            FileTextSearchRequest(root="/workspace", pattern="needle", max_matches=10)
        )
        assert [match.path for match in result.matches] == ["/workspace/large.txt"]

        _write_utf8(tmp_path / "long-line.txt", "x" * 17 + " needle\n")
        with pytest.raises(EnvironmentError) as too_large:
            await environment.files.search_text(
                FileTextSearchRequest(root="/workspace", pattern="needle", max_matches=10)
            )
        assert too_large.value.code == "environment_too_large"


async def test_search_result_page_does_not_limit_file_traversal(tmp_path: Path) -> None:
    for index in range(20):
        _write_utf8(tmp_path / f"{index:02}.txt", "needle\n")
    binding = _aggregate(tmp_path)

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        first = await environment.files.search_text(
            FileTextSearchRequest(root="/workspace", pattern="needle", max_matches=2)
        )
        assert [match.path for match in first.matches] == [
            "/workspace/00.txt",
            "/workspace/01.txt",
        ]
        assert first.has_more is True


async def test_query_and_search_share_provider_conformance_and_use_one_worker_each(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_search_conformance: Any,
) -> None:
    file_search_conformance.populate(tmp_path)
    files = local_files_module.LocalFileOperator(
        root=tmp_path,
        read_only=True,
        policy=local_binding_module._DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )

    original_to_thread = local_files_module.asyncio.to_thread
    calls: list[str] = []

    async def tracked_to_thread(function, /, *args, **kwargs):
        calls.append(function.__name__)
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(local_files_module.asyncio, "to_thread", tracked_to_thread)
    await file_search_conformance.assert_operator(files, root="/")

    assert calls == ["_query_page", "_query_page", "_search_page"]


async def test_search_fails_explicitly_when_eligible_file_limit_hides_candidates(tmp_path: Path) -> None:
    _write_utf8(tmp_path / "a.py", "needle\n")
    _write_utf8(tmp_path / "b.py", "needle\n")
    files = local_files_module.LocalFileOperator(
        root=tmp_path,
        read_only=True,
        policy=local_binding_module._DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        binding_id="binding-1",
        binding_revision=1,
        generation="generation-1",
    )

    with pytest.raises(EnvironmentError) as captured:
        await files.search_text(
            FileTextSearchRequest(
                root="/",
                pattern="needle",
                include="**/*.py",
                max_matches=10,
                max_files=1,
            )
        )

    assert captured.value.code == "environment_too_large"


async def test_local_retention_is_bounded_readable_and_released(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-output",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        assert store is not None
        policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
        writer = await store.reserve(max_bytes=64)
        assert await writer.write(b"abcdefghij") == 10
        reference = await writer.commit()
        result = await store.read(reference, policy=policy)
        assert result.chunks[0].data == b"abcd"
        assert result.next_cursor is not None
        await store.release(reference=reference)
        with pytest.raises(EnvironmentError):
            await store.read(reference, policy=policy)


async def test_retention_stops_capturing_after_the_first_quota_gap(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-output-gap",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
            max_buffer_bytes=4,
            max_spool_bytes=6,
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        assert store is not None
        blocker = await store.reserve(max_bytes=64)
        assert await blocker.write(b"block!") == 6
        blocker_reference = await blocker.commit()

        writer = await store.reserve(max_bytes=64)
        assert await writer.write(b"prefix") == 0
        await store.release(reference=blocker_reference)
        assert await writer.write(b"suffix") == 0
        await writer.abort()
        assert store._used_bytes == 0


async def test_retained_read_serializes_with_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-output-race",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        assert store is not None
        writer = await store.reserve(max_bytes=64)
        assert await writer.write(b"abcdefghij") == 10
        reference = await writer.commit()
        started = threading.Event()
        proceed = threading.Event()
        original_read_range = local_retention_module._read_range

        def blocking_read_range(path: Path, offset: int, size: int) -> bytes:
            started.set()
            assert proceed.wait(timeout=1)
            return original_read_range(path, offset, size)

        monkeypatch.setattr(local_retention_module, "_read_range", blocking_read_range)
        policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
        read_task = asyncio.create_task(store.read(reference, policy=policy))
        assert await asyncio.to_thread(started.wait, 1)
        release_task = asyncio.create_task(store.release(reference=reference))
        await asyncio.sleep(0)
        assert not release_task.done()
        proceed.set()
        result = await read_task
        await release_task
        assert result.chunks[0].data == b"abcd"


async def test_retention_accounts_actual_bytes_and_refunds_release(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-output-quota",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
            max_buffer_bytes=4,
            max_spool_bytes=8,
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        assert store is not None
        first = await store.reserve(max_bytes=64)
        second = await store.reserve(max_bytes=64)
        assert store._used_bytes == 0
        assert await first.write(b"abcdef") == 6
        assert await second.write(b"ghijkl") == 2
        assert store._used_bytes == 8
        first_reference = await first.commit()
        second_reference = await second.commit()

        await store.release(reference=first_reference)
        assert store._used_bytes == 2
        third = await store.reserve(max_bytes=64)
        assert await third.write(b"mnopqr") == 6
        await third.abort()
        assert store._used_bytes == 2
        await store.release(reference=second_reference)
        assert store._used_bytes == 0


async def test_opaque_output_reference_is_exact_python_only() -> None:
    value = OpaqueOutputReference._from_payload("provider-token")

    class Container(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        reference: OpaqueOutputReference

    model = Container(reference=value)
    assert model.model_dump()["reference"] is value
    assert "provider-token" not in repr(value)
    with pytest.raises(PydanticSerializationError):
        model.model_dump_json()
    with pytest.raises(PydanticSerializationError):
        TypeAdapter(OpaqueOutputReference).dump_json(value)
    with pytest.raises(PydanticInvalidForJsonSchema):
        TypeAdapter(OpaqueOutputReference).json_schema()
    with pytest.raises(ValidationError):
        Container.model_validate({"reference": "provider-token"})
