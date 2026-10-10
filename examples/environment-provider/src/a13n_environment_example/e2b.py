"""Run with E2B_API_KEY set; always destroy the example's own sandbox."""

import asyncio
import os
import secrets

from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_environment.e2b.provider import E2B
from a13n_environment.retention import EnvironmentOutputPolicy


async def main() -> None:
    configuration = E2BEnvironmentConfiguration()
    identity = "env-example-" + secrets.token_hex(8)
    async with await E2B.open_provider(credential={"api_key": os.environ["E2B_API_KEY"]}) as provider:
        state = await provider.create(configuration, environment_id=identity, operation_id="op-create")
        connector = provider.execution_connector(configuration, environment_id=identity, state=state)
        try:
            async with await connector.open() as environment:
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
            async with await connector.open() as recovered:
                processes = recovered.operations.processes
                assert processes is not None
                listing = await processes.list(limit=50)
                process = next((item for item in listing.processes if item.handle.identity == native_identity), None)
                if process is not None:
                    print(f"recovered native command: {process.status.phase}")
                    await processes.kill(process.handle)
                else:
                    print("Native command no longer discoverable; not restarting it.")
        finally:
            await provider.destroy(configuration, environment_id=identity, state=state, operation_id="op-delete")


if __name__ == "__main__":
    asyncio.run(main())
