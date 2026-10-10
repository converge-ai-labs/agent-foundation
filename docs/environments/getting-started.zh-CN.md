---
title: 环境快速开始
sidebarTitle: 快速开始
description: 不使用 Harness、模型、Docker 或云账号，直接读写文件。
---

## 从源码安装

示例跟随 `main`，使用 Python 3.13 和仓库锁文件：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-environment
```

环境库是独立的 `a13n-environment` 包，不依赖 Harness。Docker、E2B 和 Modal 的 SDK 分别位于该包的可选依赖中。

## 运行完整文件示例

将以下代码保存为 `environment_example.py`，运行 `uv run python environment_example.py`：

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

连接配置的构造和验证不进行 I/O。`open()` 打开一个已就绪的独立执行对象；退出上下文会释放该执行对象拥有的资源。Direct Local 使用 Host 已有目录，关闭执行不会删除目录。这里的临时目录由应用负责删除。

`/hello.txt` 是该环境中的逻辑路径。Direct Local 使用 Host 账号权限，目录映射不提供操作系统隔离。

## 与 Harness 一起使用

通过 Host 来源提供 connector。每次 Run 仅在首次使用时打开独立 execution；已打开的 execution 不能作为 Run 输入：

此处 `PreparedSource` 使用 [Harness 环境指南](../a13n-harness/environments.md#supply-a-source)中的 Host 来源实现。它在首次使用时返回已准备好的 connector。

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

这段代码要求工作目录仍然存在，且 `executable` 启用了 `DynamicEnvironmentCapability`。Harness 在首次使用时打开 execution，并在 Run 结束时关闭它。仅传入环境不会自动向模型添加工具。

接下来阅读[生命周期与状态](lifecycle.md)、[操作接口](operations.md)和[Harness 环境接入](../a13n-harness/environments.md)。
