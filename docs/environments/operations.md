---
title: Files, commands, and operation limits
sidebarTitle: Files, paths, and limits
description: Choose an Environment operation, address the right files, and read bounded results.
---

Use Environment operations to read and change files, run commands, and observe processes directly from Python. Start with the complete file example in [Getting started](getting-started.md), then use this page to choose methods and interpret their results.

## Operation families

Open a connector, then select the interface under `execution.operations` for your task. Each interface is optional; the execution's descriptor lists the supported families and exact actions.

| Family    | Use it to                                              | Check before relying on the result                       |
| --------- | ------------------------------------------------------ | -------------------------------------------------------- |
| Files     | Read, write, list, search, and move files              | The Provider's path rules and access limits              |
| Shell     | Run a foreground command with bounded output           | Allowed executables, shell syntax, and supported limits  |
| Processes | Start a command and observe or control it across calls | Support for each action, including discovery and signals |
| Outputs   | Read retained stdout/stderr by offset                  | Available ranges and whether observation is complete     |
| Ports     | Observe whether a supported target is listening        | The configured address and port allowlist                |

Connector `open()` returns an execution with its advertised operation families ready. Call `check_ready()` for a later read-only check. A failure can affect one family while others remain usable; readiness checks do not repair the target.

Native Files & Shell covers file, command, process, and output work. Envd provides additional capabilities such as port observation. Direct Local also offers opt-in loopback port observation through `allowed_ports`; see [Ports and lifecycle](commands.md#ports-and-lifecycle).

## Paths belong to the boundary you are calling

Choose the path format for the API you call:

| Provider            | File path example                                              | Meaning                             |
| ------------------- | -------------------------------------------------------------- | ----------------------------------- |
| Native Local or E2B | `/hello.txt`                                                   | A path within the configured root   |
| Native Docker       | `/workspace/hello.txt`                                         | A container path                    |
| Envd                | `/home/user/hello.txt`                                         | A Device-absolute POSIX path        |
| Envd on Windows     | `/C:/Users/example/hello.txt` or `/UNC/server/share/hello.txt` | A Device-absolute drive or UNC path |

Harness adds mount selection and relative-path resolution above these Provider APIs. Pass Provider-local paths to a low-level file operator; a Harness aggregate path is usable there only if it also satisfies that Provider's rules.

A mapped root selects file paths; it does not isolate an allowed command at the OS level. Envd's fixed working directory also leaves file access governed by the Device's launch Sandbox and egress mode, within the authority granted by the outer Host.

## Environment operations and tools

Use the typed Provider interfaces when your application performs operations itself. To let an agent perform them, configure Harness Run inputs and `DynamicEnvironmentCapability` as shown in [Use Environments from Harness](../a13n-harness/environments.md).

Harness supplies mount names, routing, access ceilings, operation timeouts, and state aggregation. Model-facing tools are an additional choice: adding an Environment alone does not expose them. The Provider remains responsible for enforcing the operations it advertises.

## File operation reference

After opening an execution, obtain `execution.operations.files` and check that it is available. Select a method for the task:

| Method                                                                 | Inputs and result                                                                |
| ---------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `read_text(path, line_offset=0, line_limit=200, max_line_length=2000)` | Text, `lines_read`, `has_more`, and truncated-line evidence                      |
| `read_bytes(path, offset=0, length=None)`                              | Bytes within the Provider's value limit                                          |
| `read_bytes_stream(path, chunk_size=65536)`                            | Async iterator for binary transfer; iterate without awaiting the iterator itself |
| `write_bytes_stream(path, stream, mode=...)`                           | Async iterable of bytes, explicit write mode, and `FileWriteResult`              |
| `write_text(path, text, mode=...)`                                     | Explicit write mode, bytes written, and mutation receipt                         |
| `write_archive(path, FileArchive(...))`                                | Extract a ZIP manifest into an absent directory                                  |
| `batch(operations)`                                                    | Ordered file operations with results in the same order                           |
| `patch_text(path, patch)`                                              | Text patch and applied-hunk receipt                                              |
| `stat(path)`                                                           | Kind, optional size, and writability                                             |
| `list(path, max_results=..., offset=0, include_hidden=False)`          | Shallow entries and `has_more`                                                   |
| `query(FileQueryRequest(...))`                                         | Pattern-selected metadata                                                        |
| `search_text(FileTextSearchRequest(...))`                              | Text matches with one-based line and context positions                           |
| `mkdir(path, parents=False, exist_ok=False)`                           | Explicit parent/existence behavior                                               |
| `move(source, destination, replace=False)`                             | Same-Environment move                                                            |
| `copy(source, destination, replace=False)`                             | Same-Environment copy with bytes copied                                          |
| `remove(path, recursive=False)`                                        | Explicit recursive deletion policy                                               |

For writes, choose `create` for a new file, `replace` for an existing file, `upsert` for either, or `append`. The mode is required. `copy` and `move` operate within one Environment; cross-mount copy belongs to the Host or aggregate layer.

For a small sequence of changes, pass `FileStatOperation`, `FileMkdirOperation`, `FileWriteTextOperation`, or `FileRemoveOperation` to `batch`. One batch accepts up to 128 operations and 64 KiB of JSON in the same file scope. Batch writes support `create`, `replace`, and `upsert`; use an individual call for append. Stat and remove accept `missing_ok=True`. Execution stops at the first error, leaving completed operations in place. Inspect those effects before deciding what to do next: failure or cancellation triggers neither rollback nor replay.

For a directory of files, construct `FileArchive(content=zip_bytes, entries=(("SKILL.md", size),), root="")` with relative paths and expanded sizes. An optional `root` selects a prefix within the ZIP. Extract into a new candidate directory with `write_archive`, then publish the complete candidate with a separate `move`. Extraction preserves regular file contents, not archive links or permissions. Custom `FileOperator` implementations must supply both `batch` and `write_archive`.

For bounded reads and searches:

1. Set the required result limit: `max_results` for `FileQueryRequest`, or `max_matches` for `FileTextSearchRequest`. Both requests also require `root` and `pattern`.
2. Choose discovery filters. Queries recurse by default. Both requests exclude hidden entries and use `ignore_mode="none"`; select `"git"` to apply repository ignores. Text search uses `include="**/*"` by default.
3. Read `has_more`. Advance the request offset by the returned entry or match count, or by `lines_read` for text. Keep filters and the filesystem stable across pages.
4. Check truncation evidence before treating the text as complete. Text search defaults to a 64 MiB file limit and 2,000-character line limit; context can include up to 20 lines. Optional `max_matches_per_file` and `max_files` must be positive.

For command execution, continue to [Run commands and observe processes](commands.md).

## File search patterns

Use the same path syntax for file queries and text-search `include` filters on Native Local, Docker, E2B, and Envd:

| Pattern                    | Selection                                        |
| -------------------------- | ------------------------------------------------ |
| `*.py`                     | Python basenames at every traversed depth        |
| `/*.py`                    | Python names immediately below the selected root |
| `src/*.py`                 | Python names directly in `src`                   |
| `{src,tests}/**/*.{py,rs}` | Python or Rust files under either directory      |

Use a complete `**` path segment for recursion. Brace groups accept two or more nonempty alternatives, without nesting, and expand to at most 256 patterns. A pattern can contain up to 16 KiB. Backslash escapes and numeric ranges are not shell-expanded.

Text search defaults to case-sensitive literal matching at the Environment API. Harness's model-facing `grep` instead defaults to `regex=true`; select `regex=false` for literal code fragments and `case_sensitive=false` when needed. Portable regular expressions use literals, classes, grouping, alternation, anchors, and quantifiers. Native Local, Docker, and E2B use Python `re`; Envd uses Rust `regex`, which rejects lookaround and backreferences. Engine-specific extensions and Unicode edge cases can differ.

An empty result is successful. An invalid pattern returns `environment_request_invalid` with a field, reason, and correction hint. Narrow the root and include filter before raising limits. A per-file match limit caps matches from that file; exceeding an eligible-file scan ceiling raises a limit error instead of reporting an incomplete scan as complete.

## Waiting is not a command deadline

Choose a wait timeout to bound how long your application waits for an observation. Choose a supported command limit to stop execution after a deadline. For example, E2B has sandbox TTL and SDK request deadlines but rejects per-command `limits.wall_time_seconds` before launch.

Continue output reads from the returned offsets and inspect their available ranges and completeness evidence. Repeated waits do not reset an observation budget. Write to an application log file with an explicit retention policy when you need complete output beyond that budget.

## Recovery and portability

Keep process handles with the execution that owns them. Reopening the same target creates a fresh observation scope; old handles remain invalid. A new adapter can discover only what its Provider retained and supports discovering. Inspect the outcome of an interrupted command before taking further action, and never restart a missing command automatically.

Use [Provider-specific limits](providers.md#cloud-providers) to check backend differences and [Harness Environment tools](../a13n-harness/environments.md) for model-facing signatures and policy.
