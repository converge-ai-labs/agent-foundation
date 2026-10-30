"""Defaults reach real child processes without changing host or other executions."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from a13n_harness.environment.commands import ArgvCommand, CommandEnvironment, CommandRequest
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.environment.variables import with_defaults
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.execution import DirectLocalExecution
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.runloop.shared import RunloopEnvironmentConfiguration
from a13n_harness.providers.environment.sprites.shared import SpritesEnvironmentConfiguration
from a13n_harness.providers.environment.vercel.shared import VercelEnvironmentConfiguration

pytestmark = pytest.mark.anyio
VARIABLE = "A13N_TEST_TOOL_ENDPOINT"
VALUE = "https://example.test/a?quote=';中文=$HOME"
CHILD_SCRIPT = f"import os,json; print(json.dumps(os.getenv({VARIABLE!r}, 'missing')))"
SCRIPT = (
    f"import os,sys,subprocess,json; print(json.dumps([os.getenv({VARIABLE!r}), "
    f"json.loads(subprocess.check_output([sys.executable,'-c',{CHILD_SCRIPT!r}],text=True))]))"
)


def request(environment=None):
    return CommandRequest(
        command=ArgvCommand(executable=str(Path(sys.executable).resolve()), arguments=("-c", SCRIPT)),
        environment=environment or CommandEnvironment(),
        output_policy=EnvironmentOutputPolicy(max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"),
    )


@pytest.mark.parametrize("inherit", [False, True])
async def test_local_defaults_override_unset_children_and_host_isolation(tmp_path, monkeypatch, inherit):
    monkeypatch.setenv(VARIABLE, "host")
    config = DirectLocalEnvironmentConfiguration(
        root={"path": str(tmp_path)},
        allowed_executables=[sys.executable],
        allowed_environment_keys=[VARIABLE],
        inherit_environment=inherit,
        environment={VARIABLE: VALUE},
    )
    for attempt in range(2):
        execution = DirectLocalExecution(config, environment_id="env-defaults")
        await execution.open(execution_id=f"attempt-{attempt}")
        try:
            for patch, expected in [
                (CommandEnvironment(), VALUE),
                (CommandEnvironment(set={VARIABLE: "override"}), "override"),
                (CommandEnvironment(unset=(VARIABLE,)), None),
            ]:
                result = await execution.operations.shell.exec(request(patch))
                assert result.status.exit_code == 0
                assert json.loads(result.output.stdout.inline) == [expected, expected or "missing"]
            assert os.environ[VARIABLE] == "host"
        finally:
            await execution.close()
    with pytest.raises(ValueError, match="allowed_environment_keys"):
        DirectLocalEnvironmentConfiguration(root={"path": str(tmp_path)}, environment={VARIABLE: "denied"})


@pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")
@pytest.mark.parametrize(
    "model", [SpritesEnvironmentConfiguration, RunloopEnvironmentConfiguration, VercelEnvironmentConfiguration]
)
async def test_native_guest_defaults_are_literal_and_command_scoped(tmp_path, model):
    async def execute(argv, timeout):
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        assert process.returncode == 0, stderr
        return stdout.decode()

    config = model(root=str(tmp_path), python=sys.executable, environment={VARIABLE: VALUE})
    commands = NativeCommands(execute, config, "generation-one", "exec-one")
    for patch, expected in [
        (CommandEnvironment(), VALUE),
        (CommandEnvironment(set={VARIABLE: "override"}), "override"),
        (CommandEnvironment(unset=(VARIABLE,)), None),
    ]:
        result = await commands.exec(request(patch))
        assert result.status.exit_code == 0
        assert json.loads(result.output.stdout.inline) == [expected, expected or "missing"]
    assert config.environment == {VARIABLE: VALUE}


def test_windows_case_insensitive_merge_does_not_resurrect_unset_defaults():
    original = request(CommandEnvironment(set={"Path": "command"}, unset=("foo",)))
    updated = with_defaults(original, {"PATH": "default", "FOO": "remove", "BAR": "keep"}, windows=True)
    assert dict(updated.environment.set) == {"PATH": "command", "BAR": "keep"}
    assert dict(original.environment.set) == {"Path": "command"}


async def test_run_defaults_real_processes_are_isolated_on_shared_target(tmp_path, monkeypatch):
    from a13n_harness import AgentIdentityRef, AgentInstanceContext
    from a13n_harness.environment import EnvironmentAction, EnvironmentError, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration
    from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL

    monkeypatch.setenv(VARIABLE, "host")
    recipe = DirectLocalEnvironmentConfiguration(
        root={"path": str(tmp_path)},
        allowed_executables=[sys.executable],
        allowed_environment_keys=[VARIABLE],
        environment={VARIABLE: "recipe"},
    )
    connector = DIRECT_LOCAL.connector_factory(
        configuration=EnvironmentProviderConfiguration(),
        credential=None,
        environment=recipe,
        environment_id="shared-target",
        state=None,
        runtime=None,
    )
    from types import SimpleNamespace

    async def ready():
        return connector

    source = SimpleNamespace(
        provider_key=connector.provider_key,
        environment_id=connector.environment_id,
        descriptor=connector.descriptor,
        state=connector.state,
        ensure_ready=ready,
    )
    permissions = EnvironmentPermissionSet(
        operations=frozenset(
            {
                EnvironmentAction.SHELL_EXEC,
                EnvironmentAction.PROCESS_START,
                EnvironmentAction.PROCESS_INSPECT,
                EnvironmentAction.PROCESS_RELEASE,
            }
        )
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"), agent_instance_id="agent-1"
    )

    async def execute(label):
        values = {VARIABLE: label}
        runtime = create_environment_runtime(
            mounts={"workspace": EnvironmentMount(source=source, permission_ceiling=permissions)},
            default_mount="workspace",
            command_environment=values,
        )
        values[VARIABLE] = "mutated-after-construction"
        async with runtime.bind(thread_id=label, run_id=label, instance=instance, host_refs={}) as environment:
            await runtime._activate()
            for patch, expected in [
                (CommandEnvironment(), label),
                (CommandEnvironment(set={VARIABLE: ""}), ""),
                (CommandEnvironment(unset=(VARIABLE,)), None),
                (CommandEnvironment(), label),
            ]:
                result = await environment.shell.exec(request(patch))
                assert result.status.exit_code == 0
                assert json.loads(result.output.stdout.inline) == [
                    expected,
                    expected if expected is not None else "missing",
                ]
            started = await environment.processes.start(request())
            async with asyncio.timeout(10):
                while True:
                    info = await environment.processes.inspect(started.process.handle)
                    if info.status.phase == "exited":
                        break
                    await asyncio.sleep(0.01)
            assert info.status.exit_code == 0
            assert json.loads(info.output.stdout.inline) == [label, label]
            await environment.processes.release(started.process.handle)
            # Injection must still pass through the provider's allowlist.
            with pytest.raises(EnvironmentError) as denied:
                await environment.shell.exec(request(CommandEnvironment(set={"DENIED": "value"})))
            assert denied.value.code == "environment_denied"

    await asyncio.gather(execute("run-a"), execute("run-b"))
    assert recipe.environment == {VARIABLE: "recipe"}
    assert os.environ[VARIABLE] == "host"


def test_merged_defaults_retain_command_bounds_and_windows_ambiguity_errors():
    from a13n_harness.environment import EnvironmentError
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="too many entries"):
        with_defaults(request(CommandEnvironment(set={"EXTRA": "value"})), {f"K{i}": "v" for i in range(1024)})
    with pytest.raises(EnvironmentError, match="differing only by case"):
        with_defaults(request(), {"PATH": "first", "Path": "second"}, windows=True)
    # POSIX retains distinct spellings.
    assert dict(with_defaults(request(), {"PATH": "first", "Path": "second"}).environment.set) == {
        "PATH": "first",
        "Path": "second",
    }
