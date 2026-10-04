---
title: Run commands and observe processes
sidebarTitle: Commands and processes
description: Run bounded commands or long-lived processes in an Environment and read their output.
---

Use `environment.operations.shell` for a bounded foreground execution, or `environment.operations.processes` for a command whose observation continues across calls. These are typed Provider APIs, not the model-facing `shell_exec` / `shell_wait` tool signatures.

Configure command access explicitly: Direct Local runs no command until you set `allowed_executables` or shell profiles. The file-only Direct Local quickstart does not enable execution. Direct Local runs as your Host account; a mapped directory is not a shell sandbox.

## Execute a configured executable

This complete example runs the current Python interpreter against an application-owned temporary directory. It needs no Agent, network, container, or model account:

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy


async def main() -> None:
    (direct_local,) = select_builtin_environment_providers(("direct_local",))
    with TemporaryDirectory(prefix="a13n-command-") as temporary:
        executable = str(Path(sys.executable).resolve())
        environment = await direct_local.create(
            {
                "root": {"path": str(Path(temporary).resolve())},
                "allowed_executables": [executable],
            },
            environment_id="command-example",
        )
        async with environment:
            await environment.ensure_ready(frozenset({"shell"}))
            shell = environment.operations.shell
            if shell is None:
                raise RuntimeError("Shell operations are unavailable")
            result = await shell.exec(
                CommandRequest(
                    command=ArgvCommand(
                        executable=executable,
                        arguments=("-c", "print('hello from command')"),
                    ),
                    cwd="/",
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=4096,
                        max_output_bytes=4096,
                        overflow="fail",
                    ),
                )
            )
            assert result.status.exit_code == 0
            assert result.output.stdout.inline == b"hello from command\n"
            print(result.output.stdout.inline.decode(), end="")


asyncio.run(main())
```

`ArgvCommand` sends one executable and argument tuple without implicit shell parsing. Its eligibility comes from Provider configuration. For shell syntax, use `ShellCommand(profile_id=..., script=..., login=...)` and a configured shell profile; an arbitrary executable path is not a profile ID.

## Command request fields

| Field             | Default / meaning                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------- |
| `command`         | Required `ArgvCommand` or `ShellCommand`                                                                |
| `cwd`             | Optional logical working path in this Environment                                                       |
| `environment`     | `CommandEnvironment(set={}, unset=())`; changes are checked against Provider policy                     |
| `network`         | `configured`; `deny` requests network denial, and a Provider that cannot enforce it rejects the command |
| `limits`          | `CommandLimits()` with optional wall time, stdin bytes, process count, memory, and CPU time             |
| `initial_stdin`   | Optional bytes supplied at launch                                                                       |
| `keep_stdin_open` | False; select true when later stdin writes are intended                                                 |
| `output_policy`   | Required finite inline/total bounds and overflow policy                                                 |

Environment variable keys must be unique across set/unset and cannot contain NUL or `=`. Values cannot contain NUL. At most 1,024 environment entries are accepted. Command strings cannot be empty or contain NUL; command shape is bounded to 1,025 values and 1 MiB encoded bytes. Provider-specific validation can narrow it further.

Requested limits must be positive and finite where applicable. Unsupported limits fail rather than silently becoming enforced. E2B, for example, does not support a per-command wall-time deadline even though its request timeout and sandbox TTL exist.

## Start, inspect, wait, and release

After `ensure_ready({"processes"})`, obtain the optional `processes` facet. Its methods are:

| Method                                               | Behavior                                                                                 |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `start(request)`                                     | Return `ProcessStartResult` with a bound process handle and receipt                      |
| `list(limit=...)`                                    | Bounded discovery where supported; does not attach output                                |
| `rebind(identity, output_policy=...)`                | Explicitly attach an eligible retained native process where supported                    |
| `inspect(handle)`                                    | Read current status and available metadata                                               |
| `wait(handle, condition=..., timeout_seconds=...)`   | Wait for `initial_terminal` or `tree_cleaned`; a wait deadline is not a command deadline |
| `read_output(handle, ..., policy=...)`               | Bounded stdout/stderr observation using explicit cursors or offsets                      |
| `write_stdin(handle, data, close_after_write=False)` | Write bytes and report accepted count/input state                                        |
| `close_stdin(handle)`                                | Close input without reading output                                                       |
| `signal(handle, "interrupt" or "terminate")`         | Supported cooperative control                                                            |
| `kill(handle)`                                       | Separate forceful control operation                                                      |
| `release(handle)`                                    | Release observation; not an implicit kill                                                |

Use the exact handle returned by the Provider. A `ProcessIdentity` identifies Provider, Environment, generation, and native process, but is not a portable access grant. Do not construct opaque handle payloads or copy Harness Run-local process references into Provider calls. `rebind` support does not mean all Providers can discover/recover arbitrary old processes.

Status includes phase, optional termination reason/exit code/signal/timestamps, and cleanup outcome. An initial exit can precede process-tree cleanup. Missing or unknown status is not evidence that a command never ran. Always distinguish command completion, output completion, and cleanup completion.

## Read retained output

`EnvironmentOutputPolicy` requires positive `max_inline_bytes`, `max_output_bytes`, and an `overflow` choice of `fail`, `truncate`, or `retain`; inline cannot exceed total. This is not `ToolOutputPolicy`, whose overflow vocabulary includes `spill` instead.

`read_output()` accepts independent stdout/stderr cursors or start offsets, optional `wait_seconds=0`, and a policy. Apply returned chunks at their `start_offset` and use the returned next cursor for that stream. Output reads are observations, not mutations that consume bytes. Inspect `available_start` / `available_end`, capture kind, coverage, origin, dropped/produced counts, and completion flags rather than treating every preview as complete.

The separate `outputs` facet reads and releases returned retained output references. A process handle, output reference, and output cursor are different bound values. Scope/generation checks still apply. SDK-text observations may have unknown producer totals or incomplete history; repeatedly waiting does not restore discarded bytes.

When full output matters beyond an observation budget, write an application log file and manage its retention explicitly. Never automatically restart a command after a disconnect, missing handle, or output eviction.

## Ports and lifecycle

The optional `ports` facet supports `inspect(PortTarget(...))` and `wait(target, desired="listening" or "not_listening", timeout_seconds=...)`. `PortTarget` carries optional alias, address selection (`loopback` by default or `any`), and port 1–65,535. This is observation, not a public ingress/tunnel service or permission to expose a port.

Provider configuration and actual descriptors determine support. Keep [lifecycle recovery](lifecycle.md) separate from command replay, and close a fresh adapter after each independent scope. Close releases local resources; it does not imply retained target destruction.
