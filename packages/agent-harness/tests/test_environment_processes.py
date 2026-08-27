from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import a13n_harness.environment.local.processes as local_processes_module
import a13n_harness.environment.local.retention as local_retention_module
import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    PortTarget,
    ShellCommand,
    create_environment_run_binding,
)
from a13n_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding

pytestmark = pytest.mark.anyio
requires_posix_processes = pytest.mark.skipif(
    os.name != "posix",
    reason="Direct Local process groups require POSIX.",
)


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-1",
    )


def _binding(
    root: Path,
    *,
    executables: frozenset[Path] = frozenset(),
    environment_keys: frozenset[str] = frozenset(),
    max_concurrent_processes: int = 2,
    shell_profiles: tuple[DirectLocalShellProfile, ...] = (),
    outputs: tuple[int, int] | None = None,
    ports: frozenset[int] | None = None,
    permissions: frozenset[EnvironmentAction] = frozenset(EnvironmentAction),
):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-process",
            root=DirectLocalRootConfiguration(path=root),
            shell_profiles=shell_profiles,
            allowed_executables=executables,
            allowed_environment_keys=environment_keys,
            max_concurrent_processes=max_concurrent_processes,
            max_wall_time_seconds=2,
            terminate_grace_seconds=0.02,
            max_buffer_bytes=outputs[0] if outputs is not None else 1024 * 1024,
            max_spool_bytes=outputs[1] if outputs is not None else 64 * 1024 * 1024 * 1024,
            allowed_ports=ports or frozenset(),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=permissions),
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


def _output_policy(*, overflow: str = "truncate") -> EnvironmentOutputPolicy:
    return EnvironmentOutputPolicy(
        max_inline_bytes=64,
        max_output_bytes=1024,
        overflow=overflow,
    )


@requires_posix_processes
async def test_foreground_argv_uses_minimal_environment_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    monkeypatch.setenv("AMBIENT_SECRET", "must-not-leak")
    binding = _binding(
        tmp_path,
        executables=frozenset({executable}),
        environment_keys=frozenset({"EXPLICIT"}),
    )
    script = (
        "import os,sys; "
        "sys.stdout.write(os.getenv('AMBIENT_SECRET', 'missing') + '|' + os.getenv('EXPLICIT', '')); "
        "sys.exit(7)"
    )
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", script)),
        environment=CommandEnvironment(set={"EXPLICIT": "visible"}),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        assert result.status.phase == "exited"
        assert result.status.exit_code == 7
        assert result.output.stdout.inline == b"missing|visible"


@requires_posix_processes
async def test_spawn_permission_error_is_normalized(tmp_path: Path) -> None:
    executable = tmp_path / "not-executable"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o644)
    binding = _binding(tmp_path, executables=frozenset({executable.resolve()}))

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied:
            await environment.processes.start(
                CommandRequest(
                    command=ArgvCommand(executable=str(executable.resolve())),
                    output_policy=_output_policy(),
                )
            )

    assert denied.value.code == "environment_denied"


@requires_posix_processes
async def test_process_count_limit_is_rejected_when_not_enforceable(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as unsupported:
            await environment.processes.start(
                CommandRequest(
                    command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
                    limits=CommandLimits(process_count=1),
                    output_policy=_output_policy(),
                )
            )

    assert unsupported.value.code == "environment_unsupported"


@requires_posix_processes
async def test_initial_terminal_wait_is_rejected_when_not_supported(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
        output_policy=_output_policy(),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        started = await environment.processes.start(request)
        with pytest.raises(EnvironmentError) as unsupported:
            await environment.processes.wait(
                started.process.handle,
                condition="initial_terminal",
                timeout_seconds=1,
            )
        assert unsupported.value.code == "environment_unsupported"
        await environment.processes.wait(
            started.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        await environment.processes.release(started.process.handle)


@requires_posix_processes
async def test_shell_profile_is_explicit_and_unlisted_executable_is_denied(tmp_path: Path) -> None:
    shell = Path("/bin/sh").resolve()
    binding = _binding(
        tmp_path,
        shell_profiles=(DirectLocalShellProfile(profile_id="posix", executable=shell),),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="posix", script="printf profile"),
                output_policy=_output_policy(),
            )
        )
        assert result.output.stdout.inline == b"profile"

        with pytest.raises(EnvironmentError) as denied:
            await environment.shell.exec(
                CommandRequest(
                    command=ArgvCommand(executable=str(Path(sys.executable).resolve())),
                    output_policy=_output_policy(),
                )
            )
        assert denied.value.code == "environment_denied"


@requires_posix_processes
async def test_foreground_capture_denial_happens_before_dispatch(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    marker = tmp_path / "must-not-exist"
    binding = _binding(
        tmp_path,
        executables=frozenset({executable}),
        permissions=frozenset({EnvironmentAction.SHELL_EXEC}),
    )
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import pathlib; pathlib.Path('must-not-exist').touch()"),
        ),
        output_policy=_output_policy(),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied:
            await environment.shell.exec_captured(request)

    assert denied.value.code == "environment_denied"
    assert not marker.exists()


@requires_posix_processes
async def test_binding_refresh_cannot_split_exec_from_output_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    replacement_root = tmp_path / "replacement"
    replacement_root.mkdir()

    def provider(environment_id: str, root: Path) -> DirectLocalEnvironmentProviderBinding:
        return DirectLocalEnvironmentProviderBinding(
            DirectLocalProviderConfiguration(
                environment_id=environment_id,
                root=DirectLocalRootConfiguration(path=root),
                allowed_executables=frozenset({executable}),
                max_wall_time_seconds=2,
                terminate_grace_seconds=0.02,
            )
        )

    initial = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider("local-refresh", tmp_path),
            ),
        ),
        default_binding_id="binding-1",
    )
    binding = create_environment_run_binding(
        initial_topology=initial,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    read_started = asyncio.Event()
    continue_read = asyncio.Event()
    original_read = local_retention_module.LocalRetentionStore.read
    blocked = False

    async def pause_first_read(store, *args, **kwargs):
        nonlocal blocked
        if not blocked:
            blocked = True
            read_started.set()
            await continue_read.wait()
        return await original_read(store, *args, **kwargs)

    monkeypatch.setattr(local_retention_module.LocalRetentionStore, "read", pause_first_read)
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain"),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        execution = asyncio.create_task(environment.shell.exec_captured(request))
        await asyncio.wait_for(read_started.wait(), timeout=1)
        refresh = asyncio.create_task(
            binding.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=2,
                    bindings=(
                        EnvironmentBindingRequest(
                            binding_id="binding-1",
                            binding_revision=2,
                            alias="local",
                            permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                            default_working_directory="/",
                            provider_binding=provider("local-refresh", replacement_root),
                        ),
                    ),
                    default_binding_id="binding-1",
                )
            )
        )
        await asyncio.sleep(0)
        continue_read.set()
        result = await execution
        await refresh

    assert result.output.stdout.inline == b"x" * 20
    assert result.output.stdout.reference is None


@requires_posix_processes
async def test_foreground_output_read_failure_preserves_completed_result_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))

    async def fail_read(store, *args, **kwargs):
        del store, args, kwargs
        raise RuntimeError("forced materialization failure")

    monkeypatch.setattr(local_retention_module.LocalRetentionStore, "read", fail_read)
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain"),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec_captured(request)

    assert result.status.phase == "exited"
    assert result.output.stdout.inline == b"xxxx"
    assert result.output.stdout.content_complete is False
    assert result.output.stdout.reference is None


@requires_posix_processes
async def test_foreground_output_cleanup_failure_does_not_replace_completed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))

    async def fail_release(store, **kwargs):
        del store, kwargs
        raise RuntimeError("forced cleanup failure")

    monkeypatch.setattr(local_retention_module.LocalRetentionStore, "release", fail_release)
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain"),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec_captured(request)

    assert result.status.phase == "exited"
    assert result.output.stdout.inline == b"x" * 20
    assert result.output.stdout.reference is None


@requires_posix_processes
async def test_foreground_retained_output_remains_readable(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=policy,
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        reference = result.output.stdout.reference
        assert reference is not None
        assert result.output.stdout.expires_at is None
        assert result.output.stdout.captured_bytes == 20
        read = await environment.outputs.read(reference, policy=policy)
        assert read.chunks[0].data == b"xxxx"
        await environment.outputs.release(reference=reference)


@requires_posix_processes
async def test_cancelled_foreground_exec_releases_unreachable_retained_output(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-cancelled-output",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({executable}),
            max_wall_time_seconds=2,
            terminate_grace_seconds=0.02,
        )
    )
    policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=(
                "-c",
                "import pathlib,sys,time; "
                "sys.stdout.buffer.write(b'x' * 20); sys.stdout.flush(); "
                "pathlib.Path('ready').touch(); time.sleep(30)",
            ),
        ),
        output_policy=policy,
    )

    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        shell = entered.operations.shell
        store = entered.operations.outputs
        assert shell is not None and store is not None
        task = asyncio.create_task(shell.exec(request))
        async with asyncio.timeout(1):
            while not (tmp_path / "ready").exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._records == {}
        assert store._used_bytes == 0


@requires_posix_processes
async def test_cancelled_second_output_reservation_releases_the_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-cancelled-reservation",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({executable}),
            max_wall_time_seconds=2,
            terminate_grace_seconds=0.02,
        )
    )
    original_reserve = local_retention_module.LocalRetentionStore.reserve
    second_reservation = asyncio.Event()
    never = asyncio.Event()
    calls = 0

    async def reserve(store, *, max_bytes: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            second_reservation.set()
            await never.wait()
        return await original_reserve(store, max_bytes=max_bytes)

    monkeypatch.setattr(local_retention_module.LocalRetentionStore, "reserve", reserve)
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain"),
    )

    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        processes = entered.operations.processes
        store = entered.operations.outputs
        assert processes is not None and store is not None
        task = asyncio.create_task(processes.start(request))
        await asyncio.wait_for(second_reservation.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._records == {}
        assert store._used_bytes == 0
        assert not tuple(store._root.iterdir())


@requires_posix_processes
async def test_terminal_cleanup_failure_record_can_be_released(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))

    async def fail_finish(collector):
        raise OSError("forced output failure")

    monkeypatch.setattr(local_processes_module._OutputCollector, "finish", fail_finish)
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        started = await environment.processes.start(request)
        with pytest.raises(OSError, match="forced output failure"):
            await environment.processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=1)
        failed = await environment.processes.inspect(started.process.handle)
        assert failed.status.cleanup == "failed"
        await environment.processes.release(started.process.handle)


@requires_posix_processes
async def test_provider_process_release_preserves_retained_output(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=(
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 20); sys.stderr.buffer.write(b'y' * 20)",
            ),
        ),
        output_policy=policy,
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        started = await environment.processes.start(request)
        completed = await environment.processes.wait(
            started.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        stdout = completed.output.stdout.reference
        stderr = completed.output.stderr.reference
        assert stdout is not None and stderr is not None

        await environment.processes.release(started.process.handle)
        with pytest.raises(EnvironmentError) as missing:
            await environment.processes.inspect(started.process.handle)
        assert missing.value.code == "environment_not_found"

        stdout_read = await environment.outputs.read(stdout, start_offset=0, policy=policy)
        stderr_read = await environment.outputs.read(stderr, start_offset=0, policy=policy)
        assert b"".join(chunk.data for chunk in stdout_read.chunks) == b"xxxx"
        assert b"".join(chunk.data for chunk in stderr_read.chunks) == b"yyyy"
        await environment.outputs.release(reference=stdout)
        await environment.outputs.release(reference=stderr)


@requires_posix_processes
async def test_process_manager_closes_active_processes_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="local-concurrent-close",
            root=DirectLocalRootConfiguration(path=tmp_path),
            allowed_executables=frozenset({executable}),
            max_concurrent_processes=2,
            max_wall_time_seconds=2,
            terminate_grace_seconds=0.02,
        )
    )
    original_terminate = local_processes_module.LocalProcessManager._terminate_process
    active_terminations = 0
    max_active_terminations = 0

    async def terminate(manager, process):
        nonlocal active_terminations, max_active_terminations
        active_terminations += 1
        max_active_terminations = max(max_active_terminations, active_terminations)
        try:
            await asyncio.sleep(0.02)
            await original_terminate(manager, process)
        finally:
            active_terminations -= 1

    monkeypatch.setattr(local_processes_module.LocalProcessManager, "_terminate_process", terminate)
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        output_policy=_output_policy(),
    )

    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        processes = entered.operations.processes
        assert processes is not None
        await processes.start(request)
        await processes.start(request)

    assert max_active_terminations == 2


@requires_posix_processes
async def test_background_stdin_wait_kill_and_release(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        echo = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
                ),
                initial_stdin=b"input",
                output_policy=_output_policy(),
            )
        )
        completed = await environment.processes.wait(
            echo.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        assert completed.output.stdout.inline == b"input"
        await environment.processes.release(echo.process.handle)

        bounded_stdin = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
                ),
                limits=CommandLimits(stdin_bytes=4),
                keep_stdin_open=True,
                output_policy=_output_policy(),
            )
        )
        await environment.processes.write_stdin(bounded_stdin.process.handle, b"ab")
        with pytest.raises(EnvironmentError) as too_large:
            await environment.processes.write_stdin(bounded_stdin.process.handle, b"cde")
        assert too_large.value.code == "environment_too_large"
        await environment.processes.close_stdin(bounded_stdin.process.handle)
        bounded_complete = await environment.processes.wait(
            bounded_stdin.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        assert bounded_complete.output.stdout.inline == b"ab"
        await environment.processes.release(bounded_stdin.process.handle)

        long_running = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import time; time.sleep(30)"),
                ),
                keep_stdin_open=True,
                output_policy=_output_policy(),
            )
        )
        killed = await environment.processes.kill(long_running.process.handle)
        assert killed.process.status.cleanup == "complete"
        assert killed.process.status.phase == "signaled"
        await environment.processes.release(long_running.process.handle)


@requires_posix_processes
async def test_output_projection_overflow_does_not_terminate_the_command(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    marker = tmp_path / "completed"
    binding = _binding(tmp_path, executables=frozenset({executable}))
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=(
                "-c",
                "import pathlib,sys; "
                "sys.stdout.buffer.write(b'x' * 100_000); sys.stdout.flush(); "
                "pathlib.Path('completed').write_text('done')",
            ),
        ),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=8, overflow="fail"),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as too_large:
            await environment.shell.exec(request)

    assert too_large.value.code == "environment_too_large"
    assert marker.read_text() == "done"


@requires_posix_processes
async def test_retained_output_uses_bounded_preview_actual_spool_and_process_paging(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(
        tmp_path,
        executables=frozenset({executable}),
        outputs=(4, 10),
    )
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=64, overflow="retain"),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        started = await environment.processes.start(request)
        completed = await environment.processes.wait(
            started.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        capture = completed.output.stdout
        assert capture.preview[0].data == b"xxxx"
        assert capture.produced_bytes == 20
        assert capture.captured_bytes == 10
        assert capture.dropped_bytes == 10
        assert capture.content_complete is False
        assert capture.expires_at is None

        read_policy = EnvironmentOutputPolicy(max_inline_bytes=3, max_output_bytes=64, overflow="retain")
        first = await environment.processes.read_output(started.process.handle, policy=read_policy)
        assert first.stdout.chunks[0].data == b"xxx"
        assert first.stdout.next_cursor is not None
        second = await environment.processes.read_output(
            started.process.handle,
            stdout_cursor=first.stdout.next_cursor,
            policy=read_policy,
        )
        assert second.stdout.chunks[0].data == b"xxx"

        reference = capture.reference
        assert reference is not None
        retained = await environment.outputs.read(reference, policy=read_policy)
        assert retained.capture.produced_bytes == 20
        assert retained.capture.captured_bytes == 10
        assert retained.capture.dropped_bytes == 10
        assert retained.capture.content_complete is False
        assert retained.next_cursor is not None
        retained_tail = await environment.outputs.read(
            reference,
            cursor=retained.next_cursor,
            policy=EnvironmentOutputPolicy(max_inline_bytes=64, max_output_bytes=64, overflow="retain"),
        )
        assert retained_tail.capture.content_complete is False
        await environment.processes.release(started.process.handle)


@requires_posix_processes
async def test_terminal_metadata_survives_while_active_slot_is_reused(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(
        tmp_path,
        executables=frozenset({executable}),
        max_concurrent_processes=1,
    )
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
        output_policy=_output_policy(),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        first = await environment.processes.start(request)
        await environment.processes.wait(first.process.handle, condition="tree_cleaned", timeout_seconds=1)

        second = await environment.processes.start(request)
        retained = await environment.processes.inspect(first.process.handle)
        assert retained.status.cleanup == "complete"
        await environment.processes.wait(second.process.handle, condition="tree_cleaned", timeout_seconds=1)
        await environment.processes.release(first.process.handle)
        await environment.processes.release(second.process.handle)


@requires_posix_processes
async def test_default_provider_wall_time_is_projected_into_the_command_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    original_start = local_processes_module.LocalProcessManager.start
    observed: list[float | None] = []

    async def capture_start(manager, request):
        observed.append(request.limits.wall_time_seconds)
        return await original_start(manager, request)

    monkeypatch.setattr(local_processes_module.LocalProcessManager, "start", capture_start)
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", "pass")),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.shell.exec(request)
        await environment.shell.exec(request.model_copy(update={"limits": CommandLimits(wall_time_seconds=10)}))
        await environment.shell.exec(request.model_copy(update={"limits": CommandLimits(wall_time_seconds=1)}))

    assert observed == [2.0, 2.0, 1.0]


@requires_posix_processes
async def test_command_wall_timeout_is_provider_owned_and_finite(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        limits=CommandLimits(wall_time_seconds=0.05),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        assert result.status.phase == "timed_out"
        assert result.status.termination_reason == "timeout"


@requires_posix_processes
async def test_active_process_handle_fences_topology_retirement(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import time; time.sleep(30)"),
                ),
                limits=CommandLimits(wall_time_seconds=2),
                output_policy=_output_policy(),
            )
        )
        removal = EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        with pytest.raises(EnvironmentError) as in_use:
            await binding.controller.apply(removal)
        assert in_use.value.code == "topology_in_use"
        assert environment.topology.topology_version == 1

        await environment.processes.kill(started.process.handle)
        await environment.processes.release(started.process.handle)
        change = await binding.controller.apply(removal)
        assert change.current_version == 2
        assert environment.topology.bindings == ()


async def test_loopback_port_observation_is_explicitly_allowlisted(tmp_path: Path) -> None:
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    socket = server.sockets[0]
    port = socket.getsockname()[1]
    binding = _binding(
        tmp_path,
        ports=frozenset({port}),
    )
    try:
        async with binding.bind(run_id="run-1", instance=_instance()) as environment:
            observed = await environment.ports.inspect(PortTarget(port=port))
            assert observed.status == "listening"
            with pytest.raises(EnvironmentError) as denied:
                await environment.ports.inspect(PortTarget(port=port + 1))
            assert denied.value.code == "environment_denied"
    finally:
        server.close()
        await server.wait_closed()
