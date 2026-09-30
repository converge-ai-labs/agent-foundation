---
title: 运行命令与观测进程
sidebarTitle: 命令与进程
description: 在环境中运行有界命令或长时间进程，并读取输出。
---

使用 `environment.operations.shell` 执行有界前台命令；需要跨调用持续观测命令时，使用 `environment.operations.processes`。这些是结构化 provider API，不是面向模型的 `shell_exec` / `shell_wait` 工具签名。

需要时必须显式配置命令访问。仅文件操作的 Direct Local 快速入门不启用执行。Direct Local 以 Host 账号运行；映射目录不是 shell 沙箱。

## 执行已配置的可执行文件

这个完整示例在应用管理的临时目录中运行当前 Python 解释器。无需 agent、网络、容器或模型账号：

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy


async def main() -> None:
    (direct_local,) = select_builtin_environment_providers(("direct_local",))
    with TemporaryDirectory(prefix="a13n-command-") as temporary:
        executable = str(Path(sys.executable).resolve())
        environment = await direct_local.create(
            {
                "root": {"path": str(Path(temporary).resolve())},
                "allowed_executables": [executable],
            },
            environment_id="command-example",
        )
        async with environment:
            await environment.ensure_ready(frozenset({"shell"}))
            shell = environment.operations.shell
            if shell is None:
                raise RuntimeError("Shell operations are unavailable")
            result = await shell.exec(
                CommandRequest(
                    command=ArgvCommand(
                        executable=executable,
                        arguments=("-c", "print('hello from command')"),
                    ),
                    cwd="/",
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=4096,
                        max_output_bytes=4096,
                        overflow="fail",
                    ),
                )
            )
            assert result.status.exit_code == 0
            assert result.output.stdout.inline == b"hello from command\n"
            print(result.output.stdout.inline.decode(), end="")


asyncio.run(main())
```

`ArgvCommand` 发送一个可执行文件和参数元组，不隐式进行 shell 解析。是否允许执行由 provider 配置决定。需要 shell 语法时，使用 `ShellCommand(profile_id=..., script=..., login=...)` 和已配置的 shell profile；任意可执行文件路径不是 profile ID。

## 命令请求字段

| 字段              | 默认值 / 含义                                                              |
| ----------------- | -------------------------------------------------------------------------- |
| `command`         | 必填 `ArgvCommand` 或 `ShellCommand`                                       |
| `cwd`             | 该环境内可选的逻辑工作路径                                                 |
| `environment`     | `CommandEnvironment(set={}, unset=())`；修改按 provider 策略检查           |
| `network`         | `configured`；`deny` 请求支持的网络拒绝，不是假定具备该能力                |
| `limits`          | `CommandLimits()`，可选设置墙钟时间、stdin 字节数、进程数、内存和 CPU 时间 |
| `initial_stdin`   | 启动时提供的可选字节                                                       |
| `keep_stdin_open` | False；计划后续写 stdin 时选择 true                                        |
| `output_policy`   | 必填、有限的内联/总量上限和溢出策略                                        |

set/unset 中的环境变量键必须互不重复，不能包含 NUL 或 `=`。值不能包含 NUL。最多接受 1,024 个环境条目。命令字符串不能为空或含 NUL；命令结构最多 1,025 个值和 1 MiB 编码字节。provider 专属验证可以进一步收窄。

请求限制在适用时必须为正数且有限。不支持的限制会失败，不会悄悄被当作已实施。例如，E2B 有请求超时和沙箱 TTL，但不支持逐命令墙钟期限。

## 启动、检查、等待与释放

调用 `ensure_ready({"processes"})` 后，获取可选 `processes` 接口。其方法如下：

| 方法                                                 | 行为                                                            |
| ---------------------------------------------------- | --------------------------------------------------------------- |
| `start(request)`                                     | 返回 `ProcessStartResult`，包含绑定的进程句柄和回执             |
| `list(limit=...)`                                    | 在支持时进行有界发现；不附加输出                                |
| `rebind(identity, output_policy=...)`                | 在支持时显式附加到符合条件的保留原生进程                        |
| `inspect(handle)`                                    | 读取当前状态和可用元数据                                        |
| `wait(handle, condition=..., timeout_seconds=...)`   | 等待 `initial_terminal` 或 `tree_cleaned`；等待期限不是命令期限 |
| `read_output(handle, ..., policy=...)`               | 使用显式游标或偏移量进行有界 stdout/stderr 观测                 |
| `write_stdin(handle, data, close_after_write=False)` | 写入字节并报告接受数量和输入状态                                |
| `close_stdin(handle)`                                | 关闭输入，不读取输出                                            |
| `signal(handle, "interrupt" or "terminate")`         | 支持的协作式控制                                                |
| `kill(handle)`                                       | 独立的强制控制操作                                              |
| `release(handle)`                                    | 释放观测，不隐式终止进程                                        |

使用 provider 返回的精确句柄。`ProcessIdentity` 标识 provider、环境、代次和原生进程，但不是可移植访问授权。不要构造不透明句柄载荷，也不要将 Harness 执行内进程引用复制进 provider 调用。支持 `rebind` 不代表所有 provider 都能发现或恢复任意旧进程。

状态包含阶段、可选终止原因/退出码/信号/时间戳和清理结果。主进程退出可能早于进程树清理。缺失或未知状态不能证明命令从未运行。始终区分命令完成、输出完成和清理完成。

## 读取保留输出

`EnvironmentOutputPolicy` 要求正数 `max_inline_bytes`、`max_output_bytes`，以及 `fail`、`truncate` 或 `retain` 的 `overflow` 选项；内联不能超过总量。它不同于 `ToolOutputPolicy`，后者的溢出选项使用 `spill`。

`read_output()` 接受独立 stdout/stderr 游标或起始偏移量、可选 `wait_seconds=0` 和策略。按 `start_offset` 应用返回块，并使用该流返回的下一游标。输出读取是观测，不是消费字节的修改。检查 `available_start` / `available_end`、捕获类别、覆盖范围、来源、丢弃/产生计数和完成标志，不要把每份预览都视为完整输出。

独立 `outputs` 接口读取并释放返回的保留输出引用。进程句柄、输出引用和输出游标是不同的绑定值。仍然需要检查资源范围和代次。SDK 文本观测可能不知道生产方总量，或历史不完整；反复等待不会恢复已丢弃字节。

需要在观测预算之外保留完整输出时，写入应用日志文件并显式管理保留。连接断开、句柄缺失或输出被驱逐后，绝不能自动重启命令。

## 端口与生命周期

可选 `ports` 接口支持 `inspect(PortTarget(...))` 和 `wait(target, desired="listening" or "not_listening", timeout_seconds=...)`。`PortTarget` 包含可选别名、地址选择（默认 `loopback` 或 `any`）和 1–65,535 的端口。这是观测，不是公共入口/隧道服务，也不是暴露端口的许可。

provider 配置和实际描述符决定支持范围。将[生命周期恢复](lifecycle.md)与命令重放分开，每个独立范围使用新适配器并在结束后关闭。关闭释放本地资源，不表示销毁保留目标。
