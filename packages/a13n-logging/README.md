# a13n Logging

`a13n-logging` is the shared logging library for Agent Foundation. The distribution is `a13n-logging`; the import package is `a13n_logging`. It requires Python 3.13 and uses standard logging plus Rich.

## Use it

```python
from a13n_logging import LogFormat, configure_logging, get_logger

configure_logging(
    log_format=LogFormat.json,
    logger_names=("my_application",),
    context={"service": "example"},
)
get_logger("my_application.tasks").info("ready", extra={"task_id": "task-example"})
```

Configure once at the executable boundary. Libraries only create namespaced loggers. JSON goes to stdout; `LogFormat.pretty` selects Rich terminal rendering. No logger namespace or root logger is configured by default, and this package does not redact secret values or store/rotate log files.

The [Logging guide](../../docs/a13n-logging/index.md) documents all seven exports, configuration defaults, field precedence, exception behavior, output ownership, and customization.

## Validate

```console
uv run --locked pytest packages/a13n-logging/tests
```

## Release boundary

`a13n-logging` publishes independently through `release/a13n-logging-v<version>`. Service and Harness releases consume it but do not republish it. See the [repository release model](../../spec/repository-model.md).
