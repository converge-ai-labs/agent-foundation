---
title: Environments
description: Mount sandboxed workspaces on Harness runs, enforce file and shell permission ceilings, and manage background processes across local and cloud targets.
---

`DynamicEnvironmentCapability` gives an Agent file, shell, and optional desktop tools backed by a Host-supplied `EnvironmentSource`. Harness opens each source lazily on first use, enforces mount permission ceilings, and detaches execution resources when the Run closes while leaving target creation and destruction to the Host.

## Supply a source

Implement `EnvironmentSource` (returning an `EnvironmentConnector` from `async def ensure_ready()`) and pass it to `run(environment=...)`:

```python title="environment_demo.py"
import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from pydantic_ai.models.test import TestModel


@dataclass(frozen=True)
class PreparedSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=TestModel(),
        capabilities=(DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        (root / "message.txt").write_text("Hello from the workspace.\n", encoding="utf-8")
        connector = DIRECT_LOCAL.execution_connector(
            {"root": {"path": str(root)}}, environment_id="env-workspace"
        )
        result = await executable.run("Read message.txt.", environment=PreparedSource(connector))
        print(result.output_or_raise())


asyncio.run(main())
```

## Mounts, paths, and permission ceilings

Pass `environment=source` to mount `/workspace`, or pass a mapping to `environments=` with `EnvironmentMount` to combine multiple directories and restrict allowed actions:

```python
from a13n_harness import EnvironmentMount
from a13n_harness.environment import FILE_READ_ACTIONS, EnvironmentPermissionSet

result = await executable.run(
    "Build output from reference data",
    environments={
        "build": build_source,
        "data": EnvironmentMount(
            data_source,
            permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS),
            working_directory="/reference",
        ),
    },
    default_environment="build",
)
```

- **Mount routing:** The default mount maps to `/workspace`; named mounts map to `/environment/{name}` (unless overridden with `mount_path`).
- **Built-in permission sets:** `FILE_READ_ACTIONS` (`view`, `ls`, `glob`, `grep`), `FILE_ACTIONS` (adds `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, `delete`), `FILE_EXECUTION_ACTIONS` (default—adds `shell_exec` and process controls), and `COMPUTER_ACTIONS` (desktop tools, opt-in).
- **OS boundary:** Permission ceilings restrict model-callable Environment operations; OS sandboxing comes from the selected backend ([Envd isolation](../a13n-envd/isolation.md), Docker, or [cloud sandboxes](../environments/index.md)).

## Shell process controls and state ownership

- **Foreground & background shell:** `shell_exec` waits up to `ToolExecutionConfig.foreground_wait_seconds` (5s default) before returning either completed output or a live process handle (`exec-*` with a Host `ToolExecutionRuntime`, or Run-local `process-*`), which the Agent follows via `shell_info`, `shell_wait` (using explicit `next_offset`), `shell_input`, and `shell_signal`.
- **Host state lifecycle:** Persist authoritative `EnvironmentState` in your Host store before returning a connector (see [Lifecycle and state](../environments/lifecycle.md)); closing a Run never destroys the underlying container or directory.
