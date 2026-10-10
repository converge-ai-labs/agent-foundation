---
title: Environments quickstart
sidebarTitle: Quickstart
description: Read and write a file without Harness, a model, Docker, or a cloud account.
---

## Install from this checkout

The examples track `main`. Use Python 3.13 and the repository lockfile:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-environment
```

`a13n-environment` is an independent package with no Harness dependency. Its `docker`, `e2b`, and `modal` extras provide the optional vendor SDKs.

## Run a complete file example

Save this as `environment_example.py`, then run `uv run python environment_example.py`:

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_environment.direct_local.provider import DIRECT_LOCAL


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

Connector construction and validation perform no I/O. `open()` returns one ready, independent execution; context exit releases its owned resources. Direct Local uses an existing Host directory and preserves it on close. The application owns this example's temporary directory and deletes it afterward.

`/hello.txt` is a logical path in this Environment. Direct Local shares the Host account; directory mapping does not provide OS isolation.

## Use the connector with Harness

Wrap the connector in a Host source. Each Run opens a fresh execution only on first use; already opened executions are not Run inputs:

Use the Host-owned `PreparedSource` implementation from the [Harness environment guide](../a13n-harness/environments.md#supply-a-source). It returns the prepared connector on first use.

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

This fragment assumes the directory still exists and `executable` enables `DynamicEnvironmentCapability`. Harness opens an execution on first use and closes it at Run exit. Supplying an Environment alone does not expose tools to the model.

Continue with [Lifecycle and state](lifecycle.md), [Operations](operations.md), and [Harness integration](../a13n-harness/environments.md).
