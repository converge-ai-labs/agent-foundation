---
title: Environments quickstart
sidebarTitle: Quickstart
description: Read and write one file through an Environment, without Harness, a model, Docker, or a cloud account. This shows the Provider boundary before introducing Agent tools.
---

## Install from this checkout

The examples track `main`. Use Python 3.13 and the locked workspace:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

For a published-version application, install `a13n-harness` with your package manager and use the matching release API. Environment Providers ship in the base distribution; only vendor SDKs live behind [extras](../a13n-harness/plugins.md#harness-extras).

## Run a complete file example

Save this as `environment_example.py`, then run `uv run python environment_example.py`:

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers


async def main() -> None:
    (direct_local,) = select_builtin_environment_providers(("direct_local",))
    # The application owns this disposable directory, not the Provider.
    with TemporaryDirectory(prefix="a13n-workspace-") as temporary:
        workspace = Path(temporary).resolve()
        environment = await direct_local.create(
            {"root": {"path": str(workspace)}},
            environment_id="example-workspace",
        )

        async with environment:
            await environment.ensure_ready(frozenset({"files"}))
            files = environment.operations.files
            if files is None:
                raise RuntimeError("This Environment does not expose files")
            await files.write_text("/hello.txt", "Hello from Environment", mode="upsert")
            text = await files.read_text("/hello.txt")
            assert text.text == "Hello from Environment"
            print(text.text)

        # Adapter close leaves Host-owned files intact.
        assert (workspace / "hello.txt").is_file()
        assert environment.dump_state() is None


if __name__ == "__main__":
    asyncio.run(main())
```

The output is `Hello from Environment`.

1. The selection explicitly names Direct Local; installing another Provider would not activate it.
2. Validation and adapter construction do not access the target.
3. `async with` enters a single-use operation scope. `ensure_ready({"files"})` prepares the target and checks the required family.
4. `/hello.txt` is a logical path within this Environment, not the Host filesystem root.
5. Context exit closes adapter resources. The application-owned temporary directory is removed later by `TemporaryDirectory`, not by Environment cleanup.

Direct Local shares the Host account. Its file mapping is not OS isolation for allowed commands.

## Use the same Provider with Harness

Create **another fresh adapter** and pass it to Harness; do not pass the already entered adapter from the example:

```python
environment = await direct_local.create(
    {"root": {"path": str(workspace)}},
    environment_id="example-workspace",
)
result = await executable.run("Inspect the workspace", environment=environment)
```

This fragment assumes the workspace still exists and `executable` was built with `DynamicEnvironmentCapability`. Harness handles entry, readiness on use, and close. Supplying an Environment alone does not add tools to a Model.

## Where to go next

- [Choose a backend](index.md) before adding isolation or remote execution.
- [Lifecycle and state](lifecycle.md) explains retained Docker and cloud-provider targets and explicit destruction.
- [Operations](operations.md) explains search syntax, output limits, and process handles.
- [Harness integration](../a13n-harness/environments.md) adds model-facing tools and multiple mounts.
