---
title: 日志
description: 为 Python 应用输出结构化日志、附加任务字段，并选择异常详情。
---

使用 `a13n-logging` 将 Python 日志发送到终端、JSON 收集器或轮转 JSON 文件。在可执行程序中配置输出，然后在应用各处使用带命名空间的 logger。

## 运行本地示例

1. 在 Python 3.13+ 应用中运行 `uv add a13n-logging` 安装包。
2. 将以下代码保存为 `logging_example.py`。
3. 运行 `uv run python logging_example.py`。

```python
from a13n_logging import LogFormat, configure_logging, get_logger, log_context

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")

with log_context(job_id="job-example"):
    logger.info("job_started", extra={"attempt": 1})
```

stdout 会输出一条 JSON 记录。其 `message` 为 `job_started`，`job_id` 为 `job-example`，`attempt` 为 `1`。记录还包含 UTC `timestamp`、`level` 和 `logger`。改用 `LogFormat.pretty` 可获得易读的 Rich 终端输出。

## 在可执行程序入口配置

在可执行程序中调用一次 `configure_logging()`，列出要配置的 Python logger 命名空间，例如 `my_application` 或 `a13n_harness`。库模块使用 `get_logger(__name__)`，将 handler 配置交给可执行程序。

| 关键字参数                    | 默认值             | 用途                                                     |
| ----------------------------- | ------------------ | -------------------------------------------------------- |
| `level: str`                  | `"INFO"`           | 设置不区分大小写的标准日志级别。无效级别会导致配置失败。 |
| `log_format: LogFormat`       | `LogFormat.pretty` | 为 stdout 选择 `pretty` 或 `json` 枚举成员。             |
| `logger_names: Sequence[str]` | `()`               | 选择命名空间。空的默认值不配置任何命名空间。             |
| `stdout: bool`                | `True`             | 将记录发送到 stdout。                                    |
| `file: LogFile \| None`       | `None`             | 添加轮转 JSON 文件，不受 stdout 格式影响。               |

配置通过 `logging.config.dictConfig()` 立即生效。选定的命名空间会获得 handler 和 `propagate=False`；无关 logger 保持启用。

如果要将 stdout 留给 stdio MCP 等协议，就只向文件写日志。配置前先创建文件的父目录：

```python
from pathlib import Path
from a13n_logging import LogFile, LogFormat, configure_logging

Path("logs").mkdir(exist_ok=True)
configure_logging(
    stdout=False,
    logger_names=("my_application",),
    file=LogFile(path=Path("logs/app.jsonl"), max_bytes=10_000_000, backups=5),
)
```

下一条记录将超过 `max_bytes` 时，活跃文件轮转到 `.1`，旧备份依次后移。`backups` 统计备份文件，不含活跃文件；两个数值都必须为正。每个进程使用独立日志路径。启用 stdout、文件或两者。需要其他输出目标时，自行配置标准库 handler。

## 绑定字段与异常

用 `log_context(**fields)` 包裹任务或请求，即可为配置的 handler 处理的每条记录附加标识符。第一个示例中，上下文块添加 `job_id`，单次日志调用添加 `attempt`。

嵌套上下文覆盖父级同名字段。显式 `extra` 和标准记录属性优先于上下文字段。并发任务拥有各自的上下文，块内启动的任务继承该块的值。自定义 handler 需添加 `a13n_logging.context.ContextFilter`，才能包含上下文字段。

`JsonFormatter` 输出紧凑 JSON，包含上文所示的标准字段及非保留额外字段。JSON 无法编码的值使用 `str(value)`。`PrettyFormatter` 通过 Rich 显示 logger、消息和排序后的 `key=value` 字段。

### 选择异常详情

需要异常消息和 traceback 时，使用 `logger.exception(...)`。需要通过异常类型和栈位置诊断失败、同时省略执行载荷时，使用 `exception_details(error)`：

```python
from a13n_logging import LogFormat, configure_logging, exception_details, get_logger

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")
try:
    raise ValueError("private execution payload")
except ValueError as error:
    logger.warning("job_failed", extra={"exceptions": exception_details(error)})
```

记录包含 `ValueError` 类型和栈位置，不含字符串 `private execution payload`。该函数遍历 cause、context 和异常组，保留父索引及可用的整数状态码或 errno，最多保留 32 个异常、每个异常 64 个栈帧。它省略消息、局部变量、源码行和响应体。文件路径和函数名仍可见；其他应用字段需自行选择，因为该函数不会对它们脱敏。

除上文使用的配置和上下文函数外，包还导出 `JsonFormatter` 和 `PrettyFormatter`，供自定义 handler 使用。Harness trace 和语义事件的接入方式见[观测](../a13n-harness/observation.md)。

## 验证

使用仓库检出时，在仓库根目录运行日志测试：

```console
uv run --locked pytest packages/a13n-logging/tests
```

Logging 独立于 Harness 和 Service 发布。参见[包 README](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-logging/README.md)和[发布模型](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)。
