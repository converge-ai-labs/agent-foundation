---
title: Logging
description: Structured terminal and JSON logging for a13n applications, configured once at the executable boundary.
---

`a13n-logging` configures standard Python logging for an application namespace. It writes Rich terminal output or JSON to stdout, optionally rotates a JSON file, and binds fields to a unit of work.

## Run a local example

Install `a13n-logging` in a Python 3.13+ application with `uv add a13n-logging`. Save this as `logging_example.py` and run `uv run python logging_example.py`:

```python
from a13n_logging import LogFormat, configure_logging, get_logger, log_context

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")

with log_context(job_id="job-example"):
    logger.info("job_started", extra={"attempt": 1})
```

The JSON record on stdout contains `timestamp` (UTC), `level`, `logger`, `message`, `job_id`, and `attempt`. Set `log_format=LogFormat.pretty` for Rich terminal output.

## Configure at the executable boundary

Call `configure_logging()` once in the executable and name the Python logger namespaces to configure. Libraries create namespaced loggers with `get_logger(__name__)`; they do not install handlers. The default `logger_names=()` configures no namespaces. Use Python module names such as `a13n_harness`, not distribution names such as `a13n-harness`.

| Keyword argument              | Default            | Effect                                                                         |
| ----------------------------- | ------------------ | ------------------------------------------------------------------------------ |
| `level: str`                  | `"INFO"`           | Case-insensitive standard logging level; an invalid level fails configuration. |
| `log_format: LogFormat`       | `LogFormat.pretty` | `pretty` (Rich) or `json` for stdout. Pass an enum member.                     |
| `logger_names: Sequence[str]` | `()`               | Namespaces receiving handlers, level, and `propagate=False`.                   |
| `stdout: bool`                | `True`             | Enable stdout output.                                                          |
| `file: LogFile \| None`       | `None`             | Add a rotating JSON file, even with pretty stdout.                             |

Configuration applies immediately through `logging.config.dictConfig()`. Unrelated loggers are not disabled. Both terminal formats use stdout. If stdout carries a protocol, such as a stdio MCP server, configure file-only output:

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

Rotation shifts the active file to `.1` when the next record would exceed `max_bytes`; `backups` excludes the active file. Both numbers must be positive. Assign each path to one process. `configure_logging()` requires stdout, a file, or both. For another destination, configure your own standard-library handler instead.

## Bound fields and exceptions

`log_context(**fields)` attaches fields to configured handlers' records in that context. Nested contexts override their parents' same-name fields, while `extra` and standard record attributes take precedence over bound fields. Context variables isolate concurrent tasks; tasks started inside a block inherit its values. A custom handler needs `a13n_logging.context.ContextFilter` to include those fields.

`JsonFormatter` emits a compact object with timestamp, level, logger, message, non-reserved extra fields, and `exception` when `exc_info` is present. `PrettyFormatter` emits the logger name and message plus sorted `key=value` fields through Rich. Unsupported JSON values use `str(value)`.

### Choose exception detail

`logger.exception(...)` includes exception messages and traceback. For routine diagnostics that need stack locations but not execution payloads, use `exception_details(error)`:

```python
from a13n_logging import LogFormat, configure_logging, exception_details, get_logger

configure_logging(log_format=LogFormat.json, logger_names=("my_application",))
logger = get_logger("my_application.jobs")
try:
    raise ValueError("private execution payload")
except ValueError as error:
    logger.warning("job_failed", extra={"exceptions": exception_details(error)})
```

The helper retains exception types, parent indexes, stack locations, and available integer status codes or errno. It follows causes, contexts, and exception groups, with at most 32 exceptions and 64 frames per exception. Messages, locals, source lines, and response bodies are omitted. File paths and function names remain visible. Other log fields are not redacted; choose application fields accordingly.

The public exports are `LogFormat`, `LogFile`, `get_logger`, `configure_logging`, `log_context`, `JsonFormatter`, `PrettyFormatter`, and `exception_details`. For Harness traces and semantic events, see [Observation](../a13n-harness/observation.md).

## Validate

From the repository root:

```console
uv run --locked pytest packages/a13n-logging/tests
```

Logging has its own release channel; it is not co-versioned with Harness or Service. See the [package README](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-logging/README.md) and [release model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
