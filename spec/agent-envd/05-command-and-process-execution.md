# Command and Process Execution

## Design Position

`agent-envd` owns every foreground and background command tree it starts. `shell.exec` waits for one owned command to finish; `process.start` returns an opaque handle to the same lifecycle. Native PIDs, wrappers, sandbox helpers, process groups, jobs, and descriptors are private implementation facts.

Every command is structured, runs under the configured isolation posture, and is registered with its output streams before the requested executable can execute. Initial-command status and whole-tree cleanup remain separate facts.

## Boundaries

| Concern                                                         | Owner                                            | Contract                                                  |
| --------------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------- |
| Command request, process handle, status, controls, and lifetime | This document                                    | Stable EIP behavior                                       |
| Filesystem/network containment                                  | [Execution Isolation](07-execution-isolation.md) | Required native backend or explicit outer-Host delegation |
| stdout/stderr spool, references, reads, and release             | [Command Output Spool](06-output-retention.md)   | Separate append-only byte streams                         |
| Provider ingress for a listening process                        | Provider adapter                                 | Outside command start and port observation                |
| Model-facing policy and durable Agent completion                | Harness and Host                                 | Never inferred from process exit                          |

## Command Request

The following shapes are serialized EIP JSON:

```python
class ExecutableName(BaseModel):
    kind: Literal["name"]
    name: str


class ExecutablePath(BaseModel):
    kind: Literal["path"]
    path: EIPPath


type ExecutableSpec = ExecutableName | ExecutablePath


class ArgvCommand(BaseModel):
    kind: Literal["argv"]
    executable_spec: ExecutableSpec
    arguments: tuple[str, ...] = ()


class ShellCommand(BaseModel):
    kind: Literal["shell"]
    profile_id: str
    script: str
    login: bool = False


type CommandSpec = ArgvCommand | ShellCommand


class CommandEnvironment(BaseModel):
    set: dict[str, str] = Field(default_factory=dict)
    unset: tuple[str, ...] = ()


class CommandLimits(BaseModel):
    wall_time_ms: int | None = None
    stdin_bytes: int | None = None
    process_count: int | None = None
    memory_bytes: int | None = None
    cpu_time_ms: int | None = None


class CommandRequest(BaseModel):
    command: CommandSpec
    cwd: EIPPath
    environment: CommandEnvironment = CommandEnvironment()
    network: Literal["configured", "deny"] = "configured"
    limits: CommandLimits = CommandLimits()
    initial_stdin: EncodedBytes | None = None
    keep_stdin_open: bool = False
```

Every string, collection, script, environment entry, stdin body, and limit is finite. NUL is invalid in command, environment, and path values.

### Executables and shells

`kind="argv"` executes exactly one selected executable with the supplied arguments and never reparses them through a shell.

- `ExecutableName` is one bare name with no path separator. Envd resolves it only through trusted configured search roots, never ambient or request-controlled `PATH`.
- `ExecutablePath` is an `EIPPath` under a configured mount that permits executable-source use. It cannot contain a native host path.

Envd canonicalizes the selected executable, validates that it is an executable regular file under the selected configured authority, and passes the resulting native path to the platform launcher. As with ordinary `posix_spawn`, `execve`, and `CreateProcess` pathname launch, this admission check does not create an immutable executable snapshot or promise portable descriptor-based execution. Another native actor with independent write authority can replace authorized path content between validation and OS launch; envd neither treats that race as pathname compare-and-swap nor adds a private copy, hard link, wrapper script, or retry path. A missing or unlaunchable final path fails command start.

Operator configuration uses this non-EIP shape:

```python
class TrustedShellProfile(BaseModel):
    profile_id: str
    display_name: str
    native_executable: str
    fixed_arguments: tuple[str, ...] = ()
    safe_base_environment: dict[str, str] = Field(default_factory=dict)
    executable_search_roots: tuple[str, ...] = ()
    max_script_bytes: int
    allow_login_mode: bool = False
```

The EIP descriptor exposes only:

```python
class ShellProfileDescriptor(BaseModel):
    profile_id: str
    display_name: str
    supports_login_mode: bool
    max_script_bytes: int
```

`kind="shell"` selects one trusted profile. Its descriptor copies `profile_id`, `display_name`, and `max_script_bytes` exactly and maps `allow_login_mode` to `supports_login_mode`. The profile owns its canonical native executable, fixed arguments, safe base environment, search roots, script ceiling, and login support. `login=true` is accepted only when allowed. EIP never accepts a native shell path or wrapper arguments, and request text is passed as script data rather than interpolated into another envd-constructed shell command.

### Working directory and environment

`cwd` must resolve at admission to an existing directory under a configured mount that permits command use. Its canonical native path is passed to the platform launcher, and its read/write posture constrains the isolation projection. Selecting a cwd does not grant another mount or a daemon-private path. Envd does not promise an immutable cwd object against an independent native actor that can replace authorized path content before OS launch.

The payload environment starts from a small daemon-owned compatibility allowlist and the selected shell profile, then applies authorized binding values and request changes. Envd forces trusted `PATH` and private `HOME`/temporary roots. It never inherits the daemon environment wholesale. Attachment credentials, `AGENT_ENVD_*`, control-channel values, dynamic-loader injection values, and ambient service credentials are removed. Request values reach only the final payload, not an isolation helper or supervisor.

### Network and resource limits

A request can only narrow configured policy. `network="deny"` is accepted when the active required backend proves per-command denial; it cannot widen configured `deny`, and it is unsupported in `disabled` mode.

Requested limits narrow finite daemon/provider ceilings. The descriptor's `execution_features` reports whether process-count, memory, CPU-time, per-command network denial, interrupt, and terminate semantics are enforceable. Requesting an unsupported optional limit fails before payload execution. Wall time, stdin bytes, command admission, and output are always finite.

## Process Status

```python
type ProcessPhase = Literal[
    "running",
    "exited",
    "signaled",
    "timed_out",
    "cancelled",
    "failed",
]


type CleanupOutcome = Literal[
    "pending",
    "complete",
    "residual_confined",
    "failed",
]


type TerminationReason = Literal[
    "exit",
    "signal",
    "timeout",
    "cancelled",
    "output_limit",
    "backend_lost",
]


class ProcessStatus(BaseModel):
    phase: ProcessPhase
    termination_reason: TerminationReason | None
    exit_code: int | None
    signal: Literal["interrupt", "terminate", "kill"] | None
    started_at: datetime
    ended_at: datetime | None
    cleanup: CleanupOutcome


class ProcessOutput(BaseModel):
    stdout: OutputInfo
    stderr: OutputInfo


class ProcessInfo(BaseModel):
    handle: ProcessHandle
    environment_id: str
    generation: int
    status: ProcessStatus
    stdin_open: bool
    output: ProcessOutput
```

`exit_code` is the requested executable's status, never a wrapper's. A raw platform signal number is not portable EIP data.

The initial executable can be terminal while descendants are still being cleaned, so terminal `phase` can coexist with `cleanup="pending"`. `cleanup="complete"` means the active backend's advertised tree-cleanup guarantee is satisfied. `residual_confined` is available only for required macOS isolation when remaining descendants are still proven Seatbelt-confined but complete exit cannot be observed. `cleanup="failed"` never claims that descendants are gone.

`phase="failed"` with `output_limit` means stdout or stderr crossed its per-stream ceiling and the tree was terminated. `backend_lost` means trustworthy supervision was lost. Neither invents an exit code.

## Start Atomicity

Before the requested executable can run, envd validates the request, reserves command/process/output capacity, establishes the selected isolation boundary, and commits command ownership plus both output references. A failure before that point starts no payload and releases the reservation.

`process.start` returns only after envd knows the requested executable reached exec successfully. `shell.exec` uses the same start boundary and then waits. If evidence is lost after possible payload release, envd retains the command record, requests cleanup, and reports `unknown_outcome`; it never retries through a weaker path or classifies possible dispatch as pre-dispatch failure. Once command ownership and selectors are committed, that terminal error includes bounded command evidence. A background start includes `process`, which makes inspect, kill, and release possible. Foreground execution includes `process_status` plus `output`, which keeps both streams readable and releasable; it also includes `process` when an owner record remains necessary for later control or reclamation. No committed command owner becomes unreachable merely because its success result could not be established. The [operation ledger](02-eip-protocol.md#operation-ledger-and-cancellation) therefore keeps the originating `shell.exec` or `process.start` evidence replayable while any process or output record disclosed by that evidence remains live.

The implementation can use a gate, supervisor, job, namespace, or another platform mechanism. The observable invariant is that request code cannot execute uncontained or unowned.

Advertising either `shell.exec` or `process.start` also requires `output.read`, `output.release`, `process.inspect`, `process.kill`, and `process.release`, so every committed command owner and output object has a reconciliation and cleanup path. This narrow dependency does not imply stdin, semantic signals, ports, or another optional method.

## Foreground Execution

```python
class ShellExecParams(BaseModel):
    context: EIPCallContext
    request: CommandRequest


class ShellExecResult(BaseModel):
    status: ProcessStatus
    output: ProcessOutput
    receipt: OperationReceipt
```

`shell.exec` returns after the initial command is terminal and cleanup has a terminal outcome, unless supervision evidence is lost. It always returns stdout and stderr references. Their bounded previews make small results immediately useful; `output.read` can retrieve complete large output after the call returns.

Timeout and cancellation close stdin, request the backend's strongest cleanup, and continue draining stdout/stderr. A proven timeout or cancellation is represented in `ProcessStatus`; uncertainty remains `unknown_outcome` with the strongest bounded receipt and command evidence.

## Background Process Methods

```python
class ProcessStartParams(BaseModel):
    context: EIPCallContext
    request: CommandRequest


class ProcessStartResult(BaseModel):
    process: ProcessInfo
    receipt: OperationReceipt


class ProcessInspectParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle


class ProcessInspectResult(BaseModel):
    process: ProcessInfo


class ProcessWriteStdinParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle
    data: EncodedBytes
    close_after_write: bool = False


class ProcessWriteStdinResult(BaseModel):
    accepted_bytes: int
    stdin_open: bool
    receipt: OperationReceipt


class ProcessCloseStdinParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle


class ProcessCloseStdinResult(BaseModel):
    stdin_open: Literal[False]
    receipt: OperationReceipt


class ProcessSignalParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle
    signal: Literal["interrupt", "terminate"]


class ProcessSignalResult(BaseModel):
    accepted: bool
    process: ProcessInfo
    receipt: OperationReceipt


class ProcessWaitParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle
    condition: Literal["initial_terminal", "tree_cleaned"]


class ProcessWaitResult(BaseModel):
    process: ProcessInfo


class ProcessKillParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle


class ProcessKillResult(BaseModel):
    process: ProcessInfo
    receipt: OperationReceipt


class ProcessReleaseParams(BaseModel):
    context: EIPCallContext
    handle: ProcessHandle


class ProcessReleaseResult(BaseModel):
    released: bool
    receipt: OperationReceipt
```

`process.inspect` is a non-draining snapshot. Both stream references are stable for the process lifetime, so output is read only through `output.read`; EIP has no duplicate `process.read_output` method or output cursor.

Stdin writes are serialized with close, bounded, and backpressured. A result reports exactly how many bytes envd accepted. Initial stdin is delivered incrementally; if start becomes ambiguous after partial delivery, the operation is not automatically repeated. `keep_stdin_open=false` closes stdin after complete initial delivery. Process termination and daemon drain also close it.

`process.signal` accepts only descriptor-advertised semantic actions. Unsupported actions fail before backend control and are never mapped to kill. `process.kill` requests the strongest tree cleanup and waits within the call deadline for the best terminal evidence.

`process.wait(condition="initial_terminal")` waits for a terminal initial-command phase. `tree_cleaned` additionally waits until cleanup is no longer pending. Timeout returns a typed error and does not mutate the process.

`process.release` is legal only after terminal cleanup. It removes the process handle and detaches its two output objects without deleting them; both references remain readable and separately releasable through `output.release`. Releasing a live or cleanup-pending process is a conflict. Releasing an individual background stream before its process record is removed is also a conflict, so a process snapshot never contains a dangling output reference.

## Lifetime and Concurrency

A process belongs to the daemon generation, not the session that started it. Carrier loss, reconnect, `session.close`, and Harness-run completion do not terminate it. A later authenticated session for the same Environment and generation can inspect and control the same handle.

Process records remain until explicit `process.release` or daemon-generation end. Detached output records remain under their independent lifetime. Envd does not evict a valid terminal process or output record to admit another command. Finite active-tree and record capacity can therefore reject new starts until callers release old records.

Per-process mutations serialize where their effects conflict: stdin write with close, signals with force cleanup, and release with terminal transition. Status and output reads can proceed concurrently from snapshots. Envd never adopts a caller-supplied PID or a native process it did not start.

Daemon shutdown requests strongest cleanup for every owned tree. No managed process is contractually allowed to outlive the daemon generation. In explicit disabled mode, the outer Host remains responsible for any descendant outside envd's truthful native cleanup target.

## Failure Semantics

| Failure                                                         | Result                                              | Payload meaning                                 |
| --------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------- |
| Invalid command, cwd, profile, environment, option, or capacity | Typed pre-dispatch error                            | No requested executable starts                  |
| Required containment cannot be established                      | `execution_isolation_failed`                        | No weaker fallback                              |
| Selected executable cannot exec                                 | `command_start_failed`                              | No successful process handle                    |
| Evidence lost after possible release                            | `unknown_outcome` plus receipt and command evidence | Inspect/kill/read/release; never blind retry    |
| Initial executable exits nonzero                                | Successful EIP result with exit status              | Command failure is not protocol failure         |
| Wall-time or cancellation cleanup is proven                     | Timed-out or cancelled status                       | Output remains readable                         |
| Output ceiling crossed                                          | Failed status with `output_limit`                   | Retained prefixes remain; completeness is false |
| Supervision is lost                                             | `backend_lost` and strongest cleanup                | No fabricated status                            |
| Initial command ends but tree cleanup fails                     | `cleanup="failed"` or `cleanup_failed`              | Descendant exit is not assumed                  |
| Daemon generation ends                                          | All handles and output references become stale      | No adoption by a later daemon                   |

## Compatibility

Command shapes, executable selection, process phases, termination reasons, cleanup outcomes, semantic signals, start publication, output-reference behavior, and generation lifetime are EIP compatibility facts. Platform backends can change without a wire revision when they preserve those facts and report optional support truthfully.

## Invariants

01. One command owner controls every foreground and background command tree through terminal cleanup.
02. No requested executable runs before command, process, isolation, and output ownership are committed.
03. Arguments are structured values; shell text uses only an explicit trusted profile.
04. Request values and daemon secrets never leak into helpers or ambient payload environment.
05. Unsupported enforcement options fail before payload execution.
06. `process.start` returns no handle until requested-executable exec success is known.
07. Initial-command status, wrapper status, tree cleanup, EIP delivery, and Host completion are separate facts.
08. Every command has stable stdout and stderr references; complete output within the configured ceiling remains readable after completion through `output.read`.
09. Session loss never terminates a process, while daemon-generation end terminates every owned tree and invalidates every handle.
10. Process and output records are reclaimed explicitly, never silently retargeted or evicted.
