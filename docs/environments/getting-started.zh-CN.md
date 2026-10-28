---
title: 环境快速开始
sidebarTitle: 快速开始
description: 打开本地工作区、写入文件、读回内容，并关闭执行。
---

本例通过 Direct Local 操作临时目录，需要 Python 3.13 和 `uv`，无需模型、守护进程、Docker 或云账号。

## 从源码安装

克隆仓库，使用锁文件安装 Harness。已有检出时，在仓库根目录运行最后一条命令即可：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

执行 API 属于 `a13n-harness`。可选厂商 SDK 位于它的 `docker`、`e2b` 和 `modal` extras 中，本地示例不需要这些依赖。

## 运行完整文件示例

1. 将以下程序保存为仓库根目录中的 `environment_example.py`。
2. 运行 `uv run python environment_example.py`。
3. 确认程序输出 `Hello from Environment` 并成功退出。

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

按三个阶段理解程序：

1. `TemporaryDirectory` 创建 Host 目录。connector 描述该目录，不进行 I/O。
2. `await connector.open()` 打开已就绪的执行。它的文件操作接口将逻辑路径 `/hello.txt` 映射到 `workspace / "hello.txt"`。
3. 离开异步上下文后，execution 关闭。随后的断言证明文件仍存在；外层 `TemporaryDirectory` 块退出后才删除目录。

Direct Local 使用 Host 账号。目录映射是路径接口，不提供 OS 隔离。需要沙箱目标时，继续阅读[后端选择](index.md#choose-a-backend)。

## 与 Harness 一起使用

要让 Agent 使用工作区，参照[完整 Harness 来源示例](../a13n-harness/environments.md#supply-a-source)。它提供 Host 自有的 `PreparedSource`，并启用 `DynamicEnvironmentCapability` 以提供文件工具。

在已经定义这些对象的异步应用中，将来源传给 Run：

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

保持目标目录存活，直到 Run 结束。在上面的程序中，这意味着应在 `TemporaryDirectory` 块内运行 Agent。Harness 在首次使用时打开新的 execution，在 Run 退出时关闭它；capability 提供模型可调用的工具。

接下来通过[操作](operations.md)了解文件 API，通过[生命周期与状态](lifecycle.md)了解目标复用，或按 [Harness 集成](../a13n-harness/environments.md)完成 Agent 工作流。
