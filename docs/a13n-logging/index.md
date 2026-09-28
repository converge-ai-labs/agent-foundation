# Logging

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

Call `configure_logging()` once in the executable, naming the Python logger namespaces you want to configure. Libraries only call `get_logger(__name__)` (or `logging.getLogger(__name__)`). Logger creation itself does not install handlers. The default `logger_names=()` configures **no** namespaces or root logger; `a13n-harness` is a distribution name, not necessarily a logger namespace.

| Keyword argument              | Default            | Effect                                                                         |
| ----------------------------- | ------------------ | ------------------------------------------------------------------------------ |
| `level: str`                  | `"INFO"`           | Case-insensitive standard logging level; an invalid level fails configuration. |
| `log_format: LogFormat`       | `LogFormat.pretty` | `pretty` (Rich) or `json` for stdout. Pass an enum member.                     |
| `logger_names: Sequence[str]` | `()`               | Namespaces receiving handlers, level, and `propagate=False`.                   |
| `stdout: bool`                | `True`             | Enable stdout output.                                                          |
| `file: LogFile \| None`       | `None`             | Add a rotating JSON file, even with pretty stdout.                             |

At least one output is required. Configuration applies `logging.config.dictConfig()` immediately, with `disable_existing_loggers=False`; unrelated loggers are not disabled. Default JSON writes to **stdout**, so do not use it on protocol stdout. Supply your own handler when another destination is required.

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

Rotation shifts the active file to `.1` when the next record would exceed `max_bytes`; `backups` excludes the active file. Both numbers must be positive. Assign each file path to one process; independent processes cannot safely rotate the same file.

## Bound fields and exceptions

`log_context(**fields)` attaches fields to configured handlers' records in that context. Nested contexts override their parents' same-name fields, while `extra` and standard record attributes take precedence over bound fields. Context variables isolate concurrent tasks; tasks started inside a block inherit its values. A custom handler needs `a13n_logging.context.ContextFilter` to include those fields.

`JsonFormatter` emits a compact object with timestamp, level, logger, message, non-reserved extra fields, and `exception` when `exc_info` is present. `PrettyFormatter` emits the logger name and message plus sorted `key=value` fields through Rich. Unsupported JSON values use `str(value)`.

`logger.exception(...)` includes exception text and traceback; neither this package nor its formatters removes secrets. For bounded diagnostics without exception messages, use `exception_details(error)`: it returns up to 32 entries with exception type, parent index, and the last 64 stack frames, plus integer status code or errno when available. It follows causes, contexts, and exception-group children. File paths and function names remain visible; messages, locals, source lines, and response bodies are omitted. Redact sensitive application data before logging it.

The public exports are `LogFormat`, `LogFile`, `get_logger`, `configure_logging`, `log_context`, `JsonFormatter`, `PrettyFormatter`, and `exception_details`. For Harness traces and semantic events, see [Observation](../a13n-harness/observation.md).

## Validate

From the repository root:

```console
uv run --locked pytest packages/a13n-logging/tests
```

Logging has its own release channel; it is not co-versioned with Harness or Service. See the [package README](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-logging/README.md) and [release model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
