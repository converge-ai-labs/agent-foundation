---
title: 运行环境
description: 将 Harness 智能体连接到本地目录、Docker/云沙箱或远程 Envd 守护进程，支持挂载权限与按需延迟准备。
---

`DynamicEnvironmentCapability` 为智能体提供由 `EnvironmentRuntime` 支撑的文件、Shell 及可选桌面工具。通过向 `run()` 或 `stream()` 传入 `EnvironmentSource` 或 `EnvironmentMount`，由宿主掌控环境准备与权限边界。

## 提供环境来源

实现 `EnvironmentSource`（或包装已准备好的连接器），将工作区交给本次 Run：

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


# 直接传入，或用 EnvironmentMount 控制路径与权限上限：
mount = EnvironmentMount(
    alias="workspace",
    source=PreparedSource(connector),
    permission_ceiling=EnvironmentPermissionCeiling.read_write(),
)
result = await executable.run("List files", environment=mount)
```

- `workspace` 为默认挂载（`/workspace`）；其他命名挂载（如 `data`）位于 `/mnt/data`。
- 默认延迟准备——除非显式要求预热，否则在首次调用工具时才准备环境。

## 权限与安全

- **权限上限：** `EnvironmentMount.permission_ceiling` 限制智能体在该挂载上可执行的 `EnvironmentAction`（文件读写、Shell、桌面等）。
- **多挂载组合：** 传入 `EnvironmentMount` 元组，可将只读参考目录与可写工作区组合使用。
- **清理：** Run 结束时 Harness 自动分离连接并执行清理钩子；长生命周期沙箱的停止与销毁由宿主负责。

## 相关指南

- [运行环境概览](../environments/index.md) —— Local、Docker、Kubernetes 与云提供方
- [远程 Envd](../environments/remote-envd.md) —— 通过 HTTP 或反向 WebSocket 连接外部机器
