---
title: 日志
description: 在可执行程序入口统一配置，为 a13n 应用提供结构化终端和 JSON 日志。
---

`a13n-logging` 为应用命名空间配置标准 Python logging。它向 stdout 写入 Rich 终端输出或 JSON，可选轮转 JSON 文件，并为一个工作单元绑定字段。

## 运行本地示例

在 Python 3.13+ 应用中运行 `uv add a13n-logging` 安装。将以下代码保存为 `logging_example.py`，运行 `uv run python logging_example.py`：

```python
from a13n_logging import LogFormat, configure_logging, get_logger, log_context

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")

with log_context(job_id="job-example"):
    logger.info("job_started", extra={"attempt": 1})
```

stdout 上的 JSON 记录包含 `timestamp`（UTC）、`level`、`logger`、`message`、`job_id` 和 `attempt`。设置 `log_format=LogFormat.pretty` 可使用 Rich 终端输出。

## 在可执行程序入口配置

在可执行程序中调用一次 `configure_logging()`，指定要配置的 Python logger 命名空间。库只调用 `get_logger(__name__)`（或 `logging.getLogger(__name__)`）。创建 logger 本身不会安装 handler。默认 `logger_names=()` **不配置任何** 命名空间或根 logger；`a13n-harness` 是分发包名称，不一定是 logger 命名空间。

| 关键字参数                    | 默认值             | 效果                                                    |
| ----------------------------- | ------------------ | ------------------------------------------------------- |
| `level: str`                  | `"INFO"`           | 不区分大小写的标准日志级别；级别无效会导致配置失败。    |
| `log_format: LogFormat`       | `LogFormat.pretty` | stdout 使用 `pretty`（Rich）或 `json`。传入枚举成员。   |
| `logger_names: Sequence[str]` | `()`               | 接收 handler、日志级别和 `propagate=False` 的命名空间。 |
| `stdout: bool`                | `True`             | 启用 stdout 输出。                                      |
| `file: LogFile \| None`       | `None`             | 添加轮转 JSON 文件，即使 stdout 使用 pretty。           |

至少需要一个输出。配置立即调用 `logging.config.dictConfig()`，设置 `disable_existing_loggers=False`；不会禁用无关 logger。默认 JSON 写入 **stdout**，因此不能用于协议 stdout。需要其他目标时，提供自己的 handler。

```python
from pathlib import Path
from a13n_logging import LogFile, LogFormat, configure_logging

Path("logs").mkdir(exist_ok=True)
configure_logging(
    log_format=LogFormat.pretty,
    logger_names=("my_application",),
    file=LogFile(path=Path("logs/app.jsonl"), max_bytes=10_000_000, backups=5),
)
```

下一条记录将超过 `max_bytes` 时，轮转把活跃文件移到 `.1`；`backups` 不含活跃文件。两个数字都必须为正数。每个文件路径只交给一个进程；独立进程不能安全地轮转同一文件。

## 绑定字段与异常

`log_context(**fields)` 为该上下文内配置的 handler 记录附加字段。嵌套上下文覆盖父级同名字段，而 `extra` 和标准记录属性优先于绑定字段。上下文变量隔离并发任务；块内启动的任务继承其值。自定义 handler 需要 `a13n_logging.context.ContextFilter` 才能包含这些字段。

`JsonFormatter` 生成紧凑对象，包含时间戳、级别、logger、消息、非保留额外字段，以及存在 `exc_info` 时的 `exception`。`PrettyFormatter` 通过 Rich 输出 logger 名称和消息，加上排序的 `key=value` 字段。不支持的 JSON 值使用 `str(value)`。

`logger.exception(...)` 包含异常文本和 traceback；本包及其 formatter 都不会移除秘密。需要不含异常消息的有界诊断时，使用 `exception_details(error)`：它最多返回 32 个条目，包含异常类型、父索引和最后 64 个栈帧，以及可用的整数状态码或 errno。它遍历 cause、context 和异常组子项。文件路径和函数名仍可见；消息、局部变量、源码行和响应体会被省略。记录前请对敏感应用数据脱敏。

公开导出为 `LogFormat`、`LogFile`、`get_logger`、`configure_logging`、`log_context`、`JsonFormatter`、`PrettyFormatter` 和 `exception_details`。Harness trace 和语义事件见[观测](../a13n-harness/observation.md)。

## 验证

在仓库根目录运行：

```console
uv run --locked pytest packages/a13n-logging/tests
```

Logging 有自己的发布渠道，不与 Harness 或 Service 共用版本。参见[包 README](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-logging/README.md)和[发布模型](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)。
