# a13n Logging

`a13n-logging` is the shared logging library for Agent Foundation. The distribution is `a13n-logging`; the import package is `a13n_logging`. It requires Python 3.13 and uses standard logging plus Rich.

## Use it

```python
from a13n_logging import LogFormat, configure_logging, get_logger, log_context

configure_logging(
    log_format=LogFormat.json,
    logger_names=("my_application",),
)
logger = get_logger("my_application.tasks")
with log_context(task_id="task-example"):
    logger.info("ready", extra={"attempt": 1})
```

Configure once at the executable boundary. Libraries only create namespaced loggers. JSON goes to stdout; `LogFormat.pretty` selects Rich terminal rendering. A `LogFile` adds a size-rotated JSON file; use `stdout=False` with a file when stdout carries a protocol. `log_context(**fields)` binds fields within a unit of work. No namespace is configured by default.

Use `exception_details(error)` for bounded exception types and stack locations without exception messages or execution payloads. Ordinary fields and `logger.exception(...)` are not redacted.

The [Logging guide](../../docs/a13n-logging/index.md) documents the public exports, configuration defaults, bound fields and their precedence, file rotation, exception behavior, output ownership, and customization.

## Validate

```console
uv run --locked pytest packages/a13n-logging/tests
```

## Release boundary

`a13n-logging` publishes independently through `release/a13n-logging-v<version>`. Service and Harness releases consume it but do not republish it. See the [repository release model](../../spec/repository-model.md).
