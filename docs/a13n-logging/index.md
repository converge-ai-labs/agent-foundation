# Logging

`a13n-logging` supplies standard-library loggers, a process configuration builder, and pretty or JSON formatting. Use it when embedding Foundation packages in your own executable. It does not provide a log server, trace exporter, storage, rotation, or automatic secret redaction.

## Install and emit a record

```console
uv add a13n-logging
```

Python 3.13 or later is required. This complete example configures one application namespace and writes JSON to stdout:

```python
from a13n_logging import LogFormat, configure_logging, get_logger

configure_logging(
    level="INFO",
    log_format=LogFormat.json,
    logger_names=("my_application",),
    context={"service": "example-worker", "role": "worker"},
)

logger = get_logger("my_application.jobs")
logger.info("job_started", extra={"job_id": "job-example", "attempt": 1})
```

A record contains a UTC timestamp, severity, logger name, message, and the supplied structured fields. Timestamp values vary. Switch to `LogFormat.pretty` for Rich terminal output with sorted `key=value` fields.

## Configure at the process boundary

Libraries call `get_logger(__name__)` or `logging.getLogger(__name__)`; the executable configures the namespaces it owns once. Importing this package and creating a logger do not install handlers.

```python
from a13n_logging import get_logger

logger = get_logger(__name__)


def report_ready() -> None:
    logger.info("ready")
```

Calling `configure_logging()` applies `logging.config.dictConfig()` immediately. It replaces handler configuration for selected namespaces; it is not a per-request context manager. Prefer `build_logging_config()` if the host needs to combine the generated dictionary with a larger logging configuration before applying it.

The default `logger_names=()` selects **no namespaces**. The helper does not configure the root logger or infer which Foundation packages to capture. Supply actual logger namespaces; distribution names such as `a13n-harness` are not necessarily Python logger names.

## Configuration reference

`build_logging_config()` and `configure_logging()` accept the same keyword-only arguments:

| Argument                                | Default            | Meaning                                                                                 |
| --------------------------------------- | ------------------ | --------------------------------------------------------------------------------------- |
| `level: str`                            | `"INFO"`           | Uppercased before passing to standard logging; invalid levels fail during configuration |
| `log_format: LogFormat`                 | `LogFormat.pretty` | Use the enum member `pretty` or `json`, not an arbitrary string                         |
| `logger_names: Sequence[str]`           | `()`               | Exact namespaces to attach to the generated `default` handler                           |
| `context: Mapping[str, object] \| None` | `None`             | Stable process fields added through `ContextFilter`                                     |

The returned dictionary uses logging schema version `1` and `disable_existing_loggers=False`. Each selected logger receives the chosen level, the `default` handler, and `propagate=False`, preventing a second emission through ancestors. Other loggers are not disabled.

### Pretty output

`PrettyFormatter` produces `logger-name message` followed by extra fields sorted by name. Field values are compact JSON; unsupported values fall back to their string representation.

The configured handler is Rich's `RichHandler` with `markup=False`, `rich_tracebacks=True`, and `show_path=False`. Rich owns console rendering and traceback presentation. The helper exposes no console/file/rotation settings; customize the dictionary when the embedding process needs them.

### JSON output

`JsonFormatter` produces one compact Unicode JSON object per record:

| Field             | Value                                                                    |
| ----------------- | ------------------------------------------------------------------------ |
| `timestamp`       | `record.created` formatted as an ISO UTC timestamp                       |
| `level`           | Standard logging level name                                              |
| `logger`          | Logger namespace                                                         |
| `message`         | `record.getMessage()`, including standard logging argument interpolation |
| Additional fields | Non-reserved record attributes whose names do not start with `_`         |
| `exception`       | Formatted exception text when `exc_info` is present                      |

The default JSON handler is `logging.StreamHandler` targeting `sys.stdout`, not stderr. This matters in processes whose stdout carries a protocol or machine-readable command result: supply a custom handler instead of contaminating that stream.

`default=str` is used for unsupported JSON values. The formatter is not a typed telemetry schema validator or a guarantee that every arbitrary Python value meets a strict downstream JSON policy. Pass bounded, serializable application values.

## Context precedence and exceptions

`ContextFilter(fields)` adds a field only if the record does not already have that attribute. Call-site `extra` therefore takes precedence over process defaults, and built-in LogRecord attributes are not replaced by the filter. The input mapping is copied when the filter is constructed.

```python
logger.info("phase_changed", extra={"role": "control", "phase": "draining"})
```

For the initial example, this record uses `role="control"`; other records continue to inherit `role="worker"`. This is record enrichment, not mutable request-local storage.

Use `logger.exception(...)` inside an exception handler to include traceback information. JSON records add `exception`; Rich renders the exception in its terminal format. The logging package does not remove tokens, personal data, model content, or sensitive exception strings. Redact at the owner before emitting the record.

## Public API and ownership

| Export                      | Role                                                      |
| --------------------------- | --------------------------------------------------------- |
| `LogFormat`                 | Enum containing `pretty` and `json`                       |
| `get_logger(name)`          | Return the standard-library logger without configuring it |
| `build_logging_config(...)` | Return a `dictConfig` dictionary without applying it      |
| `configure_logging(...)`    | Apply that configuration to the current process           |
| `ContextFilter`             | Add non-overwriting process defaults                      |
| `JsonFormatter`             | Produce structured JSON records                           |
| `PrettyFormatter`           | Produce event-style text for Rich                         |

For Harness traces, metrics, and semantic events, see [Observation](../a13n-harness/observation.md). Logging neither creates OpenTelemetry spans nor persists `HarnessState`.

## Development and releases

Logging has its own `a13n-logging` release channel; it is not co-versioned with Harness or Service. The source version is `0.0.0`; published consumers use their declared compatible dependency ranges.

From the repository root:

```console
uv run --locked pytest packages/a13n-logging/tests
```

The [package source](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-logging) contains the complete small implementation and its focused tests.
