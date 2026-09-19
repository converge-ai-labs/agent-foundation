"""Opt-in Linux evidence for unmodified recipe roots and the actual guest helpers."""

import asyncio
import os
import subprocess

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.commands import CommandLimits, CommandRequest, ShellCommand
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy


@pytest.fixture(scope="module")
def guest_container():
    image = os.environ.get("A13N_TEST_NATIVE_GUEST_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_NATIVE_GUEST_IMAGE to an approved Linux Python image")
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            image,
            "sleep",
            "300",
        ],
        text=True,
    ).strip()
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                "mkdir -p /home/daytona /home/user /home/sprite && chown 1000:1000 /home/daytona /home/user /home/sprite",
            ],
            check=True,
        )
        yield container
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)


@pytest.mark.parametrize("key", ["daytona", "runloop", "sprites"])
def test_default_guest_workspace_is_writable_without_root(key, guest_container):
    async def scenario():
        provider = ProviderCatalog(select_builtin_environment_providers([key])).require(key)
        configuration = provider.validate_environment({})

        async def execute(argv, timeout):
            process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "--user",
                "1000:1000",
                guest_container,
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            assert process.returncode == 0, stderr.decode()
            return stdout.decode()

        commands = NativeCommands(execute, configuration, "generation-fixture", "mount-fixture")
        files = GuestFiles(commands)
        await files.write_text("/default-proof", key, mode="upsert")
        result = await commands.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="pwd; cat default-proof"),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024, max_output_bytes=1024, overflow="truncate"
                ),
            )
        )
        assert result.status.exit_code == 0 and result.status.cleanup == "complete"
        assert result.output.stdout.inline == (configuration.root + "\n" + key).encode()
        assert (await files.read_text("/default-proof")).text == key
        bounded = await commands.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="head -c 100000 /dev/zero"),
                output_policy=EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=128, overflow="truncate"),
            )
        )
        assert bounded.output.stdout.produced_bytes == 100000
        assert bounded.output.stdout.inline == bytes(128)
        timed = await commands.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="sleep 30 & echo $! > descendant; wait"),
                limits=CommandLimits(wall_time_seconds=0.3),
                output_policy=EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=128, overflow="truncate"),
            )
        )
        assert timed.status.phase == "timed_out" and timed.status.cleanup == "complete"
        assert not timed.output.stdout.content_complete
        assert timed.output.stdout.produced_bytes is None
        child = int((await files.read_text("/descendant")).text)
        # Linux /proc distinguishes a zombie awaiting container-init reap from a running descendant.
        state = await execute(
            [
                "python3",
                "-c",
                "import pathlib,sys; p=pathlib.Path('/proc')/sys.argv[1]/'stat'; print(p.read_text().split()[2] if p.exists() else 'gone')",
                str(child),
            ],
            5,
        )
        assert state.strip() in {"Z", "gone"}

    asyncio.run(scenario())
