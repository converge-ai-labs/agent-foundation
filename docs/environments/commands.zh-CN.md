---
title: 运行命令与观测进程
sidebarTitle: 命令与进程
description: 在环境中运行有界命令或长时间进程，并读取输出。
---

需要返回有界输出的前台结果时，选择 `environment.operations.shell`。需要跨调用观测或控制命令时，选择 `environment.operations.processes`。本页使用带类型的 provider API；Harness 的 `shell_exec` 和 `shell_wait` 等工具有各自的签名。

运行 Direct Local 命令前，先允许可执行文件或配置 shell profile。仅文件操作的快速入门默认禁用命令。获准命令以 Host 账号运行；映射目录不是 shell 沙箱。

## 执行已配置的可执行文件

在已安装 `a13n-harness` 的 Python 环境中：

1. 将以下程序保存为 `command_example.py`。
2. 运行 `python command_example.py`。
3. 应看到 `hello from command`，且程序成功退出。程序会同时检查退出码和捕获的 stdout。

程序允许解析后的 Python 可执行文件，将临时目录映射到 `/`，并打开一个执行范围。它在本地运行，无需 agent、网络、容器或模型账号：

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.environment.retention import EnvironmentOutputPolicy


async def main() -> None:
    (direct_local,) = select_builtin_environment_providers(("direct_local",))
    with TemporaryDirectory(prefix="a13n-command-") as temporary:
        executable = str(Path(sys.executable).resolve())
        connector = direct_local.execution_connector(
            {
                "root": {"path": str(Path(temporary).resolve())},
                "allowed_executables": [executable],
            },
            environment_id="command-example",
        )
        async with await connector.open() as environment:
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

在自己的程序中沿用示例的三项选择：允许可执行文件、选择工作路径、设置有限输出策略。`ArgvCommand` 直接传递参数元组，不做 shell 解析。使用管道或其他 shell 语法时，先配置 shell profile，再将其 ID 传给 `ShellCommand(profile_id=..., script=..., login=...)`。

## 命令请求字段

| 字段              | 默认值 / 含义                                                               |
| ----------------- | --------------------------------------------------------------------------- |
| `command`         | 必填 `ArgvCommand` 或 `ShellCommand`                                        |
| `cwd`             | 该环境内可选的逻辑工作路径                                                  |
| `environment`     | `CommandEnvironment(set={}, unset=())`；修改按 provider 策略检查            |
| `network`         | `configured`；`deny` 请求网络拒绝，无法实施网络拒绝的 provider 会拒绝该命令 |
| `limits`          | `CommandLimits()`，可选设置墙钟时间、stdin 字节数、进程数、内存和 CPU 时间  |
| `initial_stdin`   | 启动时提供的可选字节                                                        |
| `keep_stdin_open` | False；计划后续写 stdin 时选择 true                                         |
| `output_policy`   | 必填、有限的内联/总量上限和溢出策略                                         |

计划后续写入输入时，在启动前设置 `keep_stdin_open=True`。只请求 provider 支持的限制；不支持的限制会在分派前失败。例如，E2B 有请求超时和沙箱 TTL，但拒绝逐命令墙钟期限。请求的数值限制在适用时必须为正数且有限。

将请求值保持在公共上限内。环境变量键必须非空、在 set/unset 中互不重复，且不含 NUL 和 `=`；值不得含 NUL。每个请求最多接受 1,024 个环境条目。命令值必须非空且不含 NUL，最多 1,025 个值、1 MiB 编码字节。provider 策略可以进一步收窄这些上限。

## 启动、检查、等待与释放

对于长时间运行的命令，先打开执行对象并获取其可选 `processes` 接口：

1. 调用一次 `start(request)`，保留返回的句柄。
2. 通过该句柄读取输出并检查状态。需要等待完成观测时，使用有界 `wait`。
3. 应用需要停止命令时，显式使用受支持的信号或 `kill`。
4. 完成后释放观测。`release` 不表示终止进程。

只选择执行对象声明支持的操作：

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

分别判断三个结果：命令完成、输出完成、进程树清理完成。`initial_terminal` 观测命令的终止状态，此时进程树可能仍需清理。检查可用的阶段、终止原因、退出码或信号、时间戳和清理结果。`missing` 或 `unknown` 表示命令情况不确定，不是再次启动的许可。

原样使用 provider 返回的句柄。`ProcessIdentity` 标识 provider、环境、代次和原生进程，访问仍取决于当前执行范围。只在声明支持时使用 `list` 和 `rebind`，不要将 Harness 执行内进程引用传入 provider 调用。

## 读取保留输出

在启动前设置 `EnvironmentOutputPolicy`。它要求正数 `max_inline_bytes` 和 `max_output_bytes`，且内联上限不超过总量，并指定 `overflow="fail"`、`"truncate"` 或 `"retain"`。环境操作使用此策略；`ToolOutputPolicy` 的溢出选项不同，包括 `spill`。

按有界分页读取进程输出：

1. 调用 `read_output()`，传入独立的 stdout/stderr 游标或起始偏移量以及策略。需要等候输出时设置 `wait_seconds`，默认值为零。
2. 按 `start_offset` 放置每个返回块，再保留该流的下一游标。读取不消费字节，因此重复读取同一范围是安全的。
3. 检查 `available_start` 和 `available_end`、捕获类别、覆盖范围、来源、丢弃/产生计数和完成标志。单凭预览无法确定输出完整。

使用独立的 `outputs` 接口读取或释放保留输出引用。将每种绑定值传入所属 API：进程句柄、输出引用和输出游标是不同的值。普通 `shell.exec` 返回有界内联输出，并在返回前释放自身的保留资源。

SDK 文本观测可能无法确定生产方总量，或存在历史缺口。继续等待无法恢复已丢弃字节，也不会重置观测预算。需要在预算之外保留完整输出时，写入有明确保留策略的应用日志文件。连接断开或观测缺失后，先检查命令结果，不要重放命令。

## 端口与生命周期

等待服务开始监听时，如果执行对象声明支持可选 `ports` 接口，就可使用该接口。单次观测调用 `inspect(PortTarget(...))`；有界等待调用 `wait(target, desired="listening" or "not_listening", timeout_seconds=...)`。`PortTarget` 接受 1–65,535 的端口和地址选择：默认 `loopback`，支持时也可选 `any`。

Envd 提供端口观测。Direct Local 在 `allowed_ports` 非空时也会启用，包括仅文件操作的执行配置。Direct Local 只探测 `127.0.0.1` 上允许列表中的端口，并拒绝 `address="any"`。端口观测不会暴露服务或创建公共隧道。

使用完毕后关闭各执行范围。关闭释放所属资源，不会停止或销毁底层目标；进程是否存活取决于后端。将[生命周期恢复](lifecycle.md)与命令重放分开。
