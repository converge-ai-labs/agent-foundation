---
title: Logging
description: Write structured logs, attach task fields, and choose exception detail for a Python application.
---

Use `a13n-logging` to send Python logs to a terminal, a JSON collector, or a rotating JSON file. Configure output in your executable, then use namespaced loggers throughout the application.

## Run a local example

1. In a Python 3.13+ application, install the package with `uv add a13n-logging`.
2. Save the following code as `logging_example.py`.
3. Run `uv run python logging_example.py`.

```python
from a13n_logging import LogFormat, configure_logging, get_logger, log_context

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")

with log_context(job_id="job-example"):
    logger.info("job_started", extra={"attempt": 1})
```

You get one JSON record on stdout. Its `message` is `job_started`, its `job_id` is `job-example`, and its `attempt` is `1`. The record also includes a UTC `timestamp`, `level`, and `logger`. Switch to `LogFormat.pretty` for readable Rich terminal output.

## Configure at the executable boundary

Call `configure_logging()` once in your executable. Include each Python logger namespace you want to configure, such as `my_application` or `a13n_harness`. Library modules use `get_logger(__name__)` and leave handler setup to the executable.

| Keyword argument              | Default            | Use                                                                               |
| ----------------------------- | ------------------ | --------------------------------------------------------------------------------- |
| `level: str`                  | `"INFO"`           | Set a case-insensitive standard logging level. Invalid levels fail configuration. |
| `log_format: LogFormat`       | `LogFormat.pretty` | Choose the `pretty` or `json` enum member for stdout.                             |
| `logger_names: Sequence[str]` | `()`               | Select namespaces. The empty default configures none.                             |
| `stdout: bool`                | `True`             | Send records to stdout.                                                           |
| `file: LogFile \| None`       | `None`             | Add a rotating JSON file, independently of the stdout format.                     |

Configuration takes effect immediately through `logging.config.dictConfig()`. Selected namespaces receive handlers and `propagate=False`; unrelated loggers remain enabled.

To keep stdout available for a protocol such as stdio MCP, write logs only to a file. Create its parent directory before configuring logging:

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

The next record that would exceed `max_bytes` rotates the active file to `.1` and shifts older backups. `backups` counts saved files, excluding the active file; both counts must be positive. Give each process its own log path. Enable stdout, a file, or both. For other destinations, configure a standard-library handler yourself.

## Bound fields and exceptions

Wrap a job or request in `log_context(**fields)` to add its identifiers to every record handled by the configured handlers. In the first example, the block adds `job_id`; the individual call adds `attempt`.

Nested contexts override matching parent fields. Explicit `extra` and standard record attributes take precedence over context fields. Concurrent tasks have separate contexts, and tasks started inside a block inherit that block's values. Add `a13n_logging.context.ContextFilter` to a custom handler to include context fields there.

`JsonFormatter` writes compact JSON with the standard fields shown above and non-reserved extra fields. Values that JSON cannot encode use `str(value)`. `PrettyFormatter` shows the logger, message, and sorted `key=value` fields through Rich.

### Choose exception detail

Use `logger.exception(...)` when you want the exception message and traceback. To diagnose a failure using its type and stack locations while omitting execution payloads, use `exception_details(error)`:

```python
from a13n_logging import LogFormat, configure_logging, exception_details, get_logger

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")
try:
    raise ValueError("private execution payload")
except ValueError as error:
    logger.warning("job_failed", extra={"exceptions": exception_details(error)})
```

This record includes the `ValueError` type and stack locations without the string `private execution payload`. The helper follows causes, contexts, and exception groups. It keeps parent indexes and available integer status codes or errno, up to 32 exceptions and 64 frames per exception. It omits messages, locals, source lines, and response bodies. File paths and function names remain visible; select other application fields yourself because the helper does not redact them.

For custom handlers, the package exports `JsonFormatter` and `PrettyFormatter` alongside the configuration and context helpers used above. For Harness traces and semantic events, continue to [Observation](../a13n-harness/observation.md).

## Validate

When working from a repository checkout, run the logging tests from its root:

```console
uv run --locked pytest packages/a13n-logging/tests
```

Logging releases independently of Harness and Service. See the [package README](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-logging/README.md) and [release model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
