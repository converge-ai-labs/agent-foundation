"""Run with E2B_API_KEY set; always destroy the example's own sandbox."""

import asyncio
import os
import secrets

from a13n_environment_provider import E2BEnvironment, E2BProviderConfiguration, E2BProviderRuntime
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
