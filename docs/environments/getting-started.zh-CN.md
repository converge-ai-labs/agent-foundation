---
title: 环境快速入门
sidebarTitle: 快速入门
description: 无需 Harness、模型、Docker 或云账号，通过环境读写一个文件。先了解 provider 边界，再引入 agent 工具。
---

## 从当前检出安装

示例跟随 `main`。使用 Python 3.13 和锁定工作空间：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

使用发布版的应用，请通过包管理器安装 `a13n-harness` 并使用对应版本 API。环境 provider 内置于基础分发包；只有厂商 SDK 通过 [extras](../a13n-harness/plugins.md#harness-extras) 按需安装。

## 运行完整文件示例

保存为 `environment_example.py`，运行 `uv run python environment_example.py`：

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

输出为 `Hello from Environment`。

1. 选择明确指定 Direct Local；安装另一个 provider 不会自动启用它。
2. 验证和构建适配器不访问目标。
3. `async with` 进入一次性操作范围。`ensure_ready({"files"})` 准备目标并检查必需操作类别。
4. `/hello.txt` 是该环境内的逻辑路径，不是 Host 文件系统根目录。
5. 退出上下文关闭适配器资源。应用管理的临时目录随后由 `TemporaryDirectory` 删除，不由环境清理删除。

Direct Local 共享 Host 账号。其文件映射不会为获准命令提供 OS 隔离。

## 在 Harness 中使用同一 provider

创建**另一个新适配器** 并传给 Harness；不要传入示例中已经进入过的适配器：

```python
environment = await direct_local.create(
    {"root": {"path": str(workspace)}},
    environment_id="example-workspace",
)
result = await executable.run("Inspect the workspace", environment=environment)
```

此片段假定工作空间仍存在，且 `executable` 已使用 `DynamicEnvironmentCapability` 构建。Harness 负责进入、使用时就绪检查和关闭。仅提供环境不会向模型添加工具。

## 下一步

- 添加隔离或远程执行前，先[选择后端](index.md)。
- [生命周期与状态](lifecycle.md)介绍保留的 Docker 和云 provider 目标，以及显式销毁。
- [操作](operations.md)介绍搜索语法、输出限制和进程句柄。
- [Harness 集成](../a13n-harness/environments.md)添加模型工具和多个挂载。
