---
title: 运行环境
description: 为 Harness 运行挂载沙箱工作区，强制执行文件与 Shell 权限上限，并跨本地与云目标管理后台进程。
---

`DynamicEnvironmentCapability` 为智能体提供由宿主 `EnvironmentSource` 驱动的文件、Shell 与可选桌面工具。Harness 在首次使用时按需打开环境、执行挂载权限上限，并在 Run 结束时释放执行资源，而底层目标的创建与销毁由宿主负责。

## 提供环境来源

实现 `EnvironmentSource`（在 `async def ensure_ready()` 中返回 `EnvironmentConnector`），并传给 `run(environment=...)`：

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

## 多挂载、路径路由与权限上限

传入 `environment=source` 默认挂载到 `/workspace`；或通过 `environments=` 搭配 `EnvironmentMount` 组合多个目录并收窄操作权限：

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

- **挂载路径：** 默认挂载位于 `/workspace`，命名挂载位于 `/environment/{name}`（除非显式指定 `mount_path`）。
- **内置权限集：** `FILE_READ_ACTIONS`（`view`、`ls`、`glob`、`grep`）、`FILE_ACTIONS`（增加 `write`、`edit`、`multi_edit`、`mkdir`、`move`、`copy`、`delete`）、`FILE_EXECUTION_ACTIONS`（默认，增加 `shell_exec` 与进程控制）以及 `COMPUTER_ACTIONS`（桌面工具，需显式开启）。
- **系统隔离边界：** 权限上限仅约束模型可调用的环境操作；操作系统级隔离由所选后端提供（见 [Envd 隔离](../a13n-envd/isolation.md)、Docker 或[云沙箱](../environments/index.md)）。

## Shell 进程控制与状态归属

- **前后台 Shell：** `shell_exec` 默认前台等待 `ToolExecutionConfig.foreground_wait_seconds`（5 秒），超时未结束则返回活跃句柄（带宿主 `ToolExecutionRuntime` 时为 `exec-*`，否则为本轮 `process-*`），供模型通过 `shell_info`、`shell_wait`（显式传入 `next_offset`）、`shell_input` 与 `shell_signal` 继续跟进。
- **宿主状态生命周期：** 返回连接器前请在宿主存储中持久化权威 `EnvironmentState`（见[生命周期与状态](../environments/lifecycle.md)）；关闭 Run 不会销毁底层容器或目录。
