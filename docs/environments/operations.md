---
title: Files, commands, and operation limits
sidebarTitle: Files, paths, and limits
description: The typed file and command operations, path rules, and limits available on an Environment.
---

Environment operations are typed Python interfaces usable without an Agent. The available facets depend on the selected Provider and configuration; check readiness and the actual descriptor rather than inferring support from a backend name.

## Operation families

| Family    | Purpose                                                       | Important boundary                                                          |
| --------- | ------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Files     | Read/write, metadata, listing, queries, search, and mutations | Logical paths and Provider-enforced access                                  |
| Shell     | Start a command                                               | Command syntax, inheritance, deadlines, and isolation are Provider-specific |
| Processes | Inspect, wait, write input, and signal where supported        | Discovery and control are separately supported operations                   |
| Outputs   | Read retained stdout/stderr at explicit offsets               | Observation may be partial or evicted; not a durable log                    |
| Ports     | Observe supported targets                                     | Not a public ingress or automatic port-sharing service                      |

Use [Getting started](getting-started.md) for a complete file example. Entering an adapter does not prepare its backing target; call `ensure_ready()` before using the required facet. A facet can be absent even though other operations work.

## Paths belong to the boundary you are calling

The single-Environment API follows each Provider's path contract. Direct Local and root-mapped native Providers use paths within their configured root, such as `/hello.txt`. Envd Providers use Device-absolute filesystem paths: `/home/user/hello.txt`, `/C:/Users/example/hello.txt`, or `/UNC/server/share/hello.txt`. Their fixed working directory does not restrict file access.

Harness adds mount selection and relative-path resolution. Do not send an aggregate Harness path directly to a low-level Provider file operator unless it is also a valid path for that Provider. Root mapping is not OS isolation for an allowed command; Envd isolation belongs to the outer Host.

## Environment operations and tools

The Provider package owns typed files, shell, process, retained-output, and port operation contracts. An entered adapter advertises only the operation families and exact actions it can enforce.

Agent Harness applies mount names, access ceilings, routing, operation timeouts, state aggregation, and optional model-facing tools. Adding an Environment does not automatically expose tools to the model. See [Use Environments from Agent Harness](../a13n-harness/environments.md) for Run inputs and `DynamicEnvironmentCapability` configuration.

## File operation reference

After `ensure_ready({"files"})`, use the optional `environment.operations.files` facet. Paths in this table follow the selected Provider's contract; Envd paths are Device-absolute.

| Method                                                                 | Inputs and result                                                               |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `read_text(path, line_offset=0, line_limit=200, max_line_length=2000)` | `FileTextResult` with text, lines read, `has_more`, and truncated-line evidence |
| `read_bytes(path, offset=0, length=None)`                              | Bounded bytes, subject to the Provider's value limit                            |
| `read_bytes_stream(path, chunk_size=65536)`                            | Async iterator for binary transfer; do not `await` the iterator itself          |
| `write_bytes_stream(path, stream, mode=...)`                           | Async iterable of bytes, explicit write mode, and `FileWriteResult`             |
| `write_text(path, text, mode=...)`                                     | Explicit write mode, bytes written, and mutation receipt                        |
| `patch_text(path, patch)`                                              | Text patch and applied-hunk receipt                                             |
| `stat(path)`                                                           | Kind, optional size, and writability                                            |
| `list(path, max_results=..., offset=0, include_hidden=False)`          | Bounded shallow entries and `has_more`                                          |
| `query(FileQueryRequest(...))`                                         | Bounded pattern-selected metadata                                               |
| `search_text(FileTextSearchRequest(...))`                              | Bounded text matches with one-based line/context positions                      |
| `mkdir(path, parents=False, exist_ok=False)`                           | Explicit parent/existence behavior                                              |
| `move(source, destination, replace=False)`                             | Same-Environment move                                                           |
| `copy(source, destination, replace=False)`                             | Same-Environment copy with bytes copied                                         |
| `remove(path, recursive=False)`                                        | Explicit recursive deletion policy                                              |

Write modes are `create` (new file), `replace` (existing file), `upsert` (create or replace), and `append`. There is no implicit default. A receipt reports the owning operation's outcome, not a general transaction across several file operations. Cross-mount copy is a Host/aggregate responsibility, not an invented second alias argument to `FileOperator.copy()`.

`FileQueryRequest` requires `root`, `pattern`, and positive `max_results`; defaults are recursive true, hidden false, ignore mode `none`, no kind restriction, and offset zero. Choose `ignore_mode="git"` explicitly when repository ignores should apply.

`FileTextSearchRequest` requires `root`, `pattern`, and positive `max_matches`. It defaults to literal, case-sensitive search; `include="**/*"`, hidden false, ignore mode `none`, no context, and offset zero. Optional `max_matches_per_file` and `max_files` are positive bounds; default file-size bound is 64 MiB and line length is 2,000. Context is bounded to 20 lines.

Read `has_more` and advance the input offset by the number of returned entries/matches (or `lines_read` for text). Keep filters and filesystem stable across pages. A partial last line needs its truncation evidence; line count alone does not prove complete text.

For commands and process observation, follow the complete [command example and typed reference](commands.md).

## File search patterns

File query patterns and text-search `include` filters use the same path syntax on Direct Local, Docker, all six cloud providers, and envd-backed Providers:

| Pattern                    | Selection                                        |
| -------------------------- | ------------------------------------------------ |
| `*.py`                     | Python basenames at every traversed depth        |
| `/*.py`                    | Python names immediately below the selected root |
| `src/*.py`                 | Python names directly in `src`                   |
| `{src,tests}/**/*.{py,rs}` | Python or Rust files under either directory      |

Brace groups are non-nested, with at least two nonempty alternatives and at most 256 expanded patterns. Patterns accept at most 16 KiB. Backslash escapes and numeric ranges are not shell-expanded. Use a complete `**` path segment for recursive matching.

Text search defaults to literal matching at the Environment API. The model-facing Harness `grep` tool instead defaults to `regex=true`; it also exposes `regex=false` and `case_sensitive=false`. Prefer literal mode for code fragments containing punctuation. Portable regular expressions use literals, classes, grouping, alternation, anchors, and quantifiers. Direct Local, Docker, and all six cloud providers use Python `re`; envd uses Rust `regex`, which rejects lookaround and backreferences. Engine-specific extensions and Unicode edge cases can differ.

Invalid patterns produce an `environment_request_invalid` error with a safe field, reason, and correction hint. Zero results are successful. Continue bounded pages using the returned offset while keeping filters and filesystem stable. A per-file match limit caps returned matches from that file; an eligible-file scan ceiling raises a limit error rather than silently claiming a complete result. Narrow the root and include filter before increasing limits.

## Waiting is not a command deadline

A bounded wait returns what is currently known; it does not necessarily stop the command. A command deadline requires Provider support. For example, E2B has sandbox TTL and SDK request deadlines but rejects per-command `execution_timeout_seconds` before launch.

Output records report provenance, available ranges, offsets, and incomplete observations. Use returned offsets for subsequent reads. Repeated inspection or waits do not reset an observation budget. If complete output matters, direct it to an application log file with an explicit retention policy.

## Recovery and portability

A process reference belongs to its current adapter/Run observation. Reusing target state does not make old process handles valid. A fresh adapter can discover only what its Provider retained and supports discovering; never restart a missing command automatically.

See [Provider-specific limits](providers.md#cloud-providers) and [Harness Environment tools](../a13n-harness/environments.md) for model-facing signatures and policy.
