"""Run with E2B_API_KEY set; always destroy the example's own sandbox."""

import asyncio
import os
import secrets

from a13n_environment import (
    CommandRequest,
    E2BEnvironment,
    E2BProviderConfiguration,
    E2BProviderRuntime,
    EnvironmentOutputPolicy,
    ShellCommand,
)
from pydantic import SecretStr


async def main() -> None:
    configuration = E2BProviderConfiguration()
    runtime = E2BProviderRuntime(api_key=SecretStr(os.environ["E2B_API_KEY"]))
    identity = "environment-example-" + secrets.token_hex(8)
    environment = E2BEnvironment(configuration, environment_id=identity, state=None, runtime=runtime)
    try:
        await environment.prepare()
        files = environment.operations.files
        assert files is not None
        await files.write_text("/example.txt", "Hello from native E2B\n", mode="create")
        print((await files.read_text("/example.txt")).text, end="")
        print(f"provider: {environment.provider_key}")
        processes = environment.operations.processes
        assert processes is not None
        started = await processes.start(
            CommandRequest(
                command=ShellCommand(profile_id="default", script="sleep 60"),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=4096,
                    max_output_bytes=65536,
                    overflow="truncate",
                ),
            )
        )
        native_identity = started.process.handle.identity
        await environment.close()  # Disconnect observation, not the native command.
        recovered = E2BEnvironment(
            configuration,
            environment_id=identity,
            state=environment.dump_state(),
            runtime=runtime,
        )
        try:
            await recovered.prepare()
            processes = recovered.operations.processes
            assert processes is not None
            listing = await processes.list(limit=50)
            process = next((item for item in listing.processes if item.handle.identity == native_identity), None)
            if process is not None:
                print(f"recovered native command: {process.status.phase}")
                await processes.kill(process.handle)  # Termination is explicit.
            else:
                print("Native command no longer discoverable; not restarting it.")
        finally:
            await recovered.close()
    finally:
        try:
            await environment.close()
        finally:
            cleanup = E2BEnvironment(
                configuration, environment_id=identity, state=environment.dump_state(), runtime=runtime
            )
            await cleanup.destroy()


if __name__ == "__main__":
    asyncio.run(main())
