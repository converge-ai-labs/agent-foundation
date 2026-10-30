---
title: Run commands and observe processes
sidebarTitle: Commands and processes
description: Run bounded commands or long-lived processes in an Environment and read their output.
---

Choose `environment.operations.shell` when you need a foreground result with bounded output. Choose `environment.operations.processes` when you need to observe or control a command across calls. This page uses the typed Provider APIs; Harness tools such as `shell_exec` and `shell_wait` have their own signatures.

Before running a Direct Local command, allow the executable or configure a shell profile. The file-only quickstart leaves commands disabled. An allowed command runs as your Host account; the mapped directory is not a shell sandbox.

## Execute a configured executable

With `a13n-harness` installed in your Python environment:

1. Save the following program as `command_example.py`.
2. Run `python command_example.py`.
3. Expect `hello from command` and a successful exit. The program checks both the exit code and captured stdout.

The program allows the resolved Python executable, maps a temporary directory to `/`, and opens one execution scope. It runs locally without an Agent, network, container, or model account:

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.environment.retention import EnvironmentOutputPolicy


async def main() -> None:
    (direct_local,) = select_builtin_environment_providers(("direct_local",))
    with TemporaryDirectory(prefix="a13n-command-") as temporary:
        executable = str(Path(sys.executable).resolve())
        connector = direct_local.execution_connector(
            {
                "root": {"path": str(Path(temporary).resolve())},
                "allowed_executables": [executable],
            },
            environment_id="command-example",
        )
        async with await connector.open() as environment:
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

Follow the example's three decisions in your own program: allow the executable, choose its working path, and set a finite output policy. `ArgvCommand` passes the argument tuple directly, without shell parsing. To use pipes or other shell syntax, configure a shell profile and pass its ID to `ShellCommand(profile_id=..., script=..., login=...)`.

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

Set `keep_stdin_open=True` before launch if you plan to write input later. Request only limits the Provider supports: unsupported limits fail before dispatch. E2B, for example, has request timeouts and sandbox TTL but rejects a per-command wall-time deadline. Requested numeric limits must be positive and finite where applicable.

Keep request values within the shared bounds. Environment keys must be nonempty, unique across set/unset, and free of NUL and `=`; values must be free of NUL. A request accepts up to 1,024 environment entries. Command values must be nonempty and free of NUL, with at most 1,025 values and 1 MiB of encoded bytes. Provider policy can narrow these bounds.

## Start, inspect, wait, and release

For a long-running command, open an execution and obtain its optional `processes` interface:

1. Call `start(request)` once and keep the returned handle.
2. Read output and inspect status through that handle. Use a bounded `wait` when you need a completion observation.
3. If your application must stop the command, use a supported signal or `kill` explicitly.
4. Release the observation when finished. `release` does not imply termination.

Select only actions advertised by the execution:

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

Keep three outcomes separate: command completion, output completion, and process-tree cleanup. `initial_terminal` observes the command's terminal status; the tree may still need cleanup. Inspect phase, termination reason, exit code or signal, timestamps, and cleanup outcome when available. Treat `missing` or `unknown` as uncertainty about the command, not permission to launch it again.

Use the Provider's returned handle unchanged. `ProcessIdentity` identifies the Provider, Environment, generation, and native process; access still depends on the current execution. Use `list` and `rebind` only where advertised, and keep Harness Run-local process references out of Provider calls.

## Read retained output

Set `EnvironmentOutputPolicy` before launch. It requires positive `max_inline_bytes` and `max_output_bytes`, with inline no greater than total, plus `overflow="fail"`, `"truncate"`, or `"retain"`. Use this policy for Environment operations; `ToolOutputPolicy` uses a different overflow vocabulary, including `spill`.

Read process output in bounded pages:

1. Call `read_output()` with independent stdout/stderr cursors or start offsets and a policy. Set `wait_seconds` if the read should wait for output; its default is zero.
2. Place each returned chunk at its `start_offset`, then retain the next cursor for that stream. Reads do not consume bytes, so rereading the same range is safe.
3. Check `available_start` and `available_end`, capture kind, coverage, origin, dropped/produced counts, and completion flags. A preview alone does not establish complete output.

Use the separate `outputs` interface to read or release a retained output reference. Pass each bound value to its owning API: a process handle, output reference, and output cursor are distinct. Ordinary `shell.exec` returns bounded inline output and releases its own retention resources before returning.

SDK-text observations can have unknown producer totals or gaps in history. Further waits cannot recover discarded bytes or reset an observation budget. Write an application log file with explicit retention when complete output matters beyond that budget. After a disconnect or missing observation, inspect the command's outcome rather than replaying it.

## Ports and lifecycle

To wait for a service to listen, use the optional `ports` interface when the execution advertises it. Call `inspect(PortTarget(...))` for one observation, or `wait(target, desired="listening" or "not_listening", timeout_seconds=...)` for a bounded wait. `PortTarget` accepts a port from 1–65,535 and an address selection: `loopback` by default, or `any` where supported.

Envd provides port observation. Direct Local also enables it when `allowed_ports` is nonempty, including for a file-only execution. Direct Local probes only allowlisted ports on `127.0.0.1` and rejects `address="any"`. Port observation neither exposes the service nor creates a public tunnel.

Close each execution scope after use. Close releases owned resources without stopping or destroying the backing target; process survival depends on the backend. Keep [lifecycle recovery](lifecycle.md) separate from command replay.
