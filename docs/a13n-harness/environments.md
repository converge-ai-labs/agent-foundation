---
title: Environments
description: Connect Harness agents to local directories, Docker/cloud sandboxes, or remote Envd daemons with mount permissions and lazy preparation.
---

`DynamicEnvironmentCapability` gives an Agent file, shell, and optional desktop tools backed by an `EnvironmentRuntime`. Pass an `EnvironmentSource` or `EnvironmentMount` to `run()` or `stream()` so the Host controls preparation and permissions.

## Supply a source

Implement `EnvironmentSource` (or wrap an already-prepared connector) to hand a workspace to a Run:

```python
from a13n_harness.environment import (
    EnvironmentConnector,
    EnvironmentMount,
    EnvironmentPermissionCeiling,
    EnvironmentSource,
)


class PreparedSource(EnvironmentSource):
    def __init__(self, connector: EnvironmentConnector) -> None:
        self._connector = connector

    async def prepare(self, request):
        return self._connector


# Pass directly or wrap in EnvironmentMount for path & permission control:
mount = EnvironmentMount(
    alias="workspace",
    source=PreparedSource(connector),
    permission_ceiling=EnvironmentPermissionCeiling.read_write(),
)
result = await executable.run("List files", environment=mount)
```

- `workspace` is the default mount (`/workspace`); named mounts (e.g., `data`) appear at `/mnt/data`.
- Preparation is lazy by default—the source prepares on first tool use unless eager preparation is requested.

## Permissions and safety

- **Permission ceilings:** `EnvironmentMount.permission_ceiling` restricts which `EnvironmentAction` operations (file read/write, shell exec, desktop) the Agent can invoke on that mount.
- **Multiple mounts:** Pass a tuple of `EnvironmentMount` objects to combine read-only reference directories with a writable workspace.
- **Cleanup:** Harness detaches and runs finalization hooks when the Run exits; the Host owns long-lived sandbox lifecycle (stop/destroy).

## Related guides

- [Environments overview](../environments/index.md) — Local, Docker, Kubernetes, and cloud providers
- [Remote Envd](../environments/remote-envd.md) — connect to an external machine over HTTP or reverse WebSocket
