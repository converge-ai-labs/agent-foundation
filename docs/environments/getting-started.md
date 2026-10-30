---
title: Environments quickstart
sidebarTitle: Quickstart
description: Open a local workspace, write a file, read it back, and close the execution.
---

This example uses Direct Local to operate on a temporary directory. It needs Python 3.13 and `uv`; it uses no model, daemon, Docker installation, or cloud account.

## Install from this checkout

Clone the repository and install Harness using its lockfile. If you already have a checkout, run the final command from its root:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

The execution APIs are part of `a13n-harness`. Optional vendor SDKs live in its `docker`, `e2b`, and `modal` extras; the local example needs none of them.

## Run a complete file example

1. Save the following program as `environment_example.py` in the repository root.
2. Run `uv run python environment_example.py`.
3. Check that it prints `Hello from Environment` and exits successfully.

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL


async def main() -> None:
    with TemporaryDirectory(prefix="a13n-workspace-") as temporary:
        workspace = Path(temporary).resolve()
        connector = DIRECT_LOCAL.execution_connector(
            {"root": {"path": str(workspace)}},
            environment_id="env-example",
        )
        async with await connector.open() as execution:
            files = execution.operations.files
            assert files is not None
            await files.write_text("/hello.txt", "Hello from Environment", mode="upsert")
            assert (await files.read_text("/hello.txt")).text == "Hello from Environment"
            print("Hello from Environment")
        assert (workspace / "hello.txt").is_file()
        assert connector.state is None


asyncio.run(main())
```

Read the program in three stages:

1. `TemporaryDirectory` creates the Host directory. The connector describes it without performing I/O.
2. `await connector.open()` opens a ready execution. Its file operator maps logical `/hello.txt` to `workspace / "hello.txt"`.
3. Leaving the async context closes the execution. The following assertion proves the file still exists; the outer `TemporaryDirectory` block removes it afterward.

Direct Local uses the Host account. Its directory mapping is a path interface, not OS isolation. For a sandboxed target, continue to [backend selection](index.md#choose-a-backend).

## Use the connector with Harness

To expose this workspace to an Agent, use the [complete Harness source example](../a13n-harness/environments.md#supply-a-source). It supplies a Host-owned `PreparedSource` and enables `DynamicEnvironmentCapability` for file tools.

Within an async application that already defines those objects, pass the source to the Run:

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

Keep the target directory alive until the Run finishes. In the program above, that means running the Agent inside the `TemporaryDirectory` block. Harness opens a fresh execution on first use and closes it at Run exit; the capability supplies the tools the model can call.

Next, use [Operations](operations.md) for file APIs, [Lifecycle and state](lifecycle.md) for target reuse, or [Harness integration](../a13n-harness/environments.md) for the full Agent workflow.
