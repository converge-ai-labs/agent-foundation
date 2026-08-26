# Environment Interaction Protocol

## Design Position

The Environment Interaction Protocol (EIP) is the transport-neutral wire contract between a trusted requester and `agent-envd`. EIP 1.0 uses JSON-RPC 2.0 for bounded control operations and correlated raw file transfer. One versioned contract owns method names, payloads, transfer lifecycles, operation replay, typed errors, selectors, limits, and side-effect evidence across trusted stdio, Host-dialed HTTP, and outbound reverse WebSocket.

EIP is a semantic Environment protocol rather than a remote syscall interface. Canonical path resolution, bounded search, complete-candidate publication, command-tree control, command-output reads, and local-port observation execute beside the native resources. Client validation improves errors but never replaces envd enforcement.

## Boundaries

| Concern                                                                                 | Owner                                                                                  | Relationship                                  |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------- |
| JSON-RPC methods, params/results, operation identity, transfers, errors, and versioning | This document                                                                          | Identical on every carrier                    |
| Canonical IDL and generated Rust/Python realization                                     | [Protocol Source, Client, and Generation](08-protocol-source-client-and-generation.md) | Encodes this contract without semantic drift  |
| Framing, attachment authentication, carrier direction, sessions, and liveness           | [Transports and Sessions](03-transports-and-sessions.md)                               | Establishes a trusted session before dispatch |
| Multi-Environment routing and Harness policy                                            | Harness and Host                                                                       | Selects one trusted binding before EIP        |
| Native filesystem, process, isolation, output, and receipt evidence                     | `agent-envd` resource owners                                                           | Executes accepted operations                  |
| Provider lifecycle effects and durable Agent completion                                 | Environment Provider package and Host                                                  | Outside EIP                                   |

Carrier headers, stdio pipes, attachment credentials, and WebSocket upgrade fields never appear in ordinary EIP params. Carrier direction cannot change a method, result, retry rule, or side-effect classification.

## Control Envelope

Every control message is one UTF-8 JSON object conforming to JSON-RPC 2.0. EIP 1.0 supports correlated request/response only, not batch arrays or application notifications. Request IDs are strings or signed 64-bit integers; booleans and wider integers are invalid. A response carries the same nullable ID and exactly one of `result` or `error`.

Raw file bytes are not control messages. They use the bounded data-frame profile after a correlated `file.open_reader` or `file.open_writer` establishes a typed transfer. A binary frame cannot name a path, create authority, commit a mutation, or invoke another method.

Small byte values that inherently belong in a JSON control method use:

```python
class EncodedBytes(BaseModel):
    encoding: Literal["base64"]
    data: str
```

`data` is canonical unpadded base64 and is decoded under the owning field's byte ceiling.

```json
{
  "jsonrpc": "2.0",
  "id": "req-42",
  "method": "shell.exec",
  "params": {
    "context": {
      "operation_id": "op-01J...",
      "timeout_ms": 30000
    },
    "request": {
      "command": {
        "kind": "argv",
        "executable_spec": {"kind": "name", "name": "git"},
        "arguments": ["status", "--short"]
      },
      "cwd": {"mount_id": "workspace", "path": "/repo"}
    }
  }
}
```

JSON bytes, nesting, strings, collections, paths, arguments, environment maps, identifiers, and encoded-byte values are bounded before domain validation. Small binary values inherent to a control method, such as one stdin chunk, use unpadded base64 in typed `EncodedBytes`. Native file content never uses JSON/base64.

Unknown JSON-RPC envelope fields are handled only as JSON-RPC permits and cannot use reserved `eip_` names. Unknown fields in typed EIP requests fail closed unless a negotiated minor explicitly permits them. Numeric byte counts, offsets, generations, ports, and durations are non-negative bounded integers.

## Initialization

`initialize` is the requester's first EIP request on every stdio, HTTP, or reverse-WebSocket session. No other method, binary data frame, or HTTP transfer body is admitted first.

The serialized wire shape is:

```python
class EIPClientInfo(BaseModel):
    name: str
    version: str


class InitializeParams(BaseModel):
    supported_protocol_versions: tuple[str, ...]
    client: EIPClientInfo
    expected_environment_id: str
    required_methods: tuple[str, ...] = ()


class EIPServerInfo(BaseModel):
    name: Literal["agent-envd"]
    version: str


class EIPLimits(BaseModel):
    max_request_bytes: int
    max_response_bytes: int
    max_concurrent_operations: int
    max_processes: int
    max_operation_duration_ms: int
    max_output_preview_bytes: int
    max_output_bytes_per_stream: int
    max_transfer_frame_bytes: int
    max_concurrent_file_transfers: int
    max_file_transfer_bytes: int


class ExecutionFeatures(BaseModel):
    process_count_limit: bool
    memory_bytes_limit: bool
    cpu_time_limit: bool
    per_command_network_deny: bool
    signal_interrupt: bool
    signal_terminate: bool


class EnvironmentDescriptor(BaseModel):
    environment_id: str
    generation: int
    mounts: tuple[MountDescriptor, ...]
    shell_profiles: tuple[ShellProfileDescriptor, ...]
    limits: EIPLimits
    isolation: IsolationPosture
    root_mount_id: str | None
    available_methods: tuple[str, ...]
    execution_features: ExecutionFeatures


class InitializeResult(BaseModel):
    protocol_version: str
    server: EIPServerInfo
    descriptor: EnvironmentDescriptor
```

Versions use `<major>.<minor>`. The server selects the highest mutually supported minor in a mutually supported major. Selecting EIP major 1 also selects binary data-frame profile version 1. No binary attachment is legal before initialization.

`expected_environment_id` is mandatory trusted binding input. A mismatch fails initialization without publishing a usable descriptor. `required_methods` contains exact JSON-RPC names. Initialization fails if any required name is absent from `available_methods`. The list does not grant a method; it asserts compatibility with the daemon's configured policy and truthful platform support.

The descriptor contains configured logical mounts only. `root_mount_id`, when present, identifies exactly one descriptor mount and is never inferred from ordering. A provider requiring broad native access configures ordinary trusted roots explicitly. There is no session-selectable resource-authority mode or synthesized server filesystem.

`available_methods` is the sole callable-method availability surface. It permits independent platform truth: omission of one unsupported mutation or process control does not hide unrelated operations. A method absent from the negotiated protocol returns `method_not_found`; a method defined by the protocol but absent from the descriptor returns `unsupported` without native dispatch.

`execution_features` qualifies only concrete optional values already present in `CommandRequest` and `ProcessSignalParams`; it is not a method catalog, authority grant, or capability family. The three limit booleans state whether `process_count`, `memory_bytes`, and `cpu_time_ms` are enforceable for every available method carrying `CommandRequest`. `per_command_network_deny` states whether `network="deny"` is accepted and enforced for this generation. Signal booleans are the accepted `process.signal` action set: the method is present exactly when at least one is true, and an action whose boolean is false returns `unsupported` before backend control dispatch. `process.kill` remains a separate exact method. Baseline wall-time and stdin/output bounds are mandatory semantics and need no optional support flag.

Only limits a client needs before constructing or dispatching work are serialized. Internal record, staging, transfer, spool, queue, and shutdown capacity remains finite daemon configuration and produces typed runtime outcomes. An absolute `expires_at` in a result is an observation, not a compatibility lease.

Initialization has no `EIPCallContext`, performs no native resource mutation, and cannot repeat within a live session.

## Common Operation Context and Replay

Every method other than `initialize` carries:

```python
class EIPCallContext(BaseModel):
    operation_id: str
    timeout_ms: int | None = None
```

`operation_id` is the one active-cancellation identity for every post-initialization EIP operation and the sole replay/receipt identity for a `terminal_evidence` operation. It is a client-generated unpredictable value of at most 128 Unicode scalar values and 512 UTF-8 bytes. The client does not intentionally assign one ID to different logical operations within a daemon generation.

The canonical IDL assigns each post-initialization method one private replay class. `initialize` has no operation context and never enters the operation ledger:

- `terminal_evidence`: operations whose side effects or controls require cross-response reconciliation retain a bounded terminal result or typed error and any receipt;
- `active_only`: observations and session-ephemeral operations use the ledger for active duplicate detection and cancellation but do not retain their response after delivery ends.

A method that produces and returns an `OperationReceipt` for its own current operation is `terminal_evidence`. `receipt.get` only observes another operation's existing receipt and remains `active_only`. `output.read` and other pure observations are also `active_only`; otherwise reading a large spool would copy every response page into the replay ledger and make output release ineffective. Session resources created by an `active_only` method remain bounded and cleaned by their session/transfer owner if the response is lost.

At admission, envd computes a canonical semantic request digest from:

- the selected EIP protocol version;
- the exact JSON-RPC method name;
- generated canonical JSON for typed params after excluding `context.operation_id` and `context.timeout_ms`.

Canonical JSON sorts object keys, emits UTF-8 without insignificant whitespace, uses EIP timestamp/integer/base64 rules, and omits absent values, schema defaults, and empty non-presence-sensitive collections. It never depends on JSON-RPC request ID, carrier data, object-key order, or a caller clock.

The ledger applies these rules atomically:

| Existing operation ID      | Method and digest | Result                                                    |
| -------------------------- | ----------------- | --------------------------------------------------------- |
| None                       | Any valid request | Insert a running entry before handler work starts         |
| Running                    | Same              | `operation_in_progress`; no duplicate dispatch            |
| Retained terminal evidence | Same              | Replay the same bounded result or typed error and receipt |
| Any retained entry         | Different         | `conflict`; no dispatch                                   |

A handler whose work ended but whose response is being published remains running. Response handoff ends only when the encoded response is accepted by the carrier's bounded writer path or that carrier is conclusively lost; handler return alone is not handoff, and handoff does not claim remote receipt. For a `terminal_evidence` method, terminal publication atomically stores exactly one replayable result or error. For an `active_only` method, the entry remains running through that handoff and is then removed without retaining response content. A terminal entry with missing evidence is never visible.

This single identity replaces a separate idempotency key or receipt selector. Exact replay reproduces the original serialized evidence but does not resurrect or extend a domain selector: a replayed handle or output reference can already be explicitly released, and every later use still revalidates its lifecycle. Retained terminal evidence normally follows finite internal reclamation policy, and its absence never proves non-dispatch. The one exception is originating `shell.exec` or `process.start` evidence that contains a selector for a still-live process or output record: envd keeps that entry replayable until every such referenced record has been explicitly released. This prevents a lost creation response from making its command owner or spool objects unreachable without retaining unrelated control history. A caller reconciles native state or accepts ambiguity rather than repeating an uncertain mutation blindly. A new observation always uses a new operation ID.

`timeout_ms`, when present, is a positive relative budget. Envd derives a monotonic deadline after admission and narrows it with the method and daemon ceiling. A retry of a retained `terminal_evidence` operation can supply another local wait budget without changing the semantic digest or extending already accepted native work. Expiry before dispatch is pre-dispatch timeout. Expiry after dispatch requests method-specific cancellation/reconciliation and does not prove a side effect absent.

## Method Availability and Catalog

The canonical IDL defines the EIP 1.0 method set:

| Domain                      | Methods                                                                                                                                                  | Replay class        | Owning contract                                                      |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | -------------------------------------------------------------------- |
| Environment/session         | `environment.describe`, `session.close`                                                                                                                  | `active_only`       | This document and [Transports](03-transports-and-sessions.md)        |
| Operation evidence          | `operation.cancel`, `receipt.get`                                                                                                                        | `active_only`       | This document                                                        |
| File observations/transfers | `file.stat`, `file.read_text`, `file.open_reader`, `file.close_reader`, `file.list`, `file.find`, `file.search`, `file.open_writer`, `file.abort_writer` | `active_only`       | [Resource Operations](04-resource-operations.md)                     |
| File mutations              | `file.write_text`, `file.commit_writer`, `file.mkdir`, `file.patch_text`, `file.copy`, `file.move`, `file.remove`                                        | `terminal_evidence` | [Resource Operations](04-resource-operations.md)                     |
| Foreground command          | `shell.exec`                                                                                                                                             | `terminal_evidence` | [Command and Process Execution](05-command-and-process-execution.md) |
| Background observations     | `process.inspect`, `process.wait`                                                                                                                        | `active_only`       | [Command and Process Execution](05-command-and-process-execution.md) |
| Background controls         | `process.start`, `process.write_stdin`, `process.close_stdin`, `process.signal`, `process.kill`, `process.release`                                       | `terminal_evidence` | [Command and Process Execution](05-command-and-process-execution.md) |
| Port observation            | `port.inspect`, `port.wait`                                                                                                                              | `active_only`       | [Resource Operations](04-resource-operations.md)                     |
| Command-output read         | `output.read`                                                                                                                                            | `active_only`       | [Command Output Spool](06-output-retention.md)                       |
| Command-output release      | `output.release`                                                                                                                                         | `terminal_evidence` | [Command Output Spool](06-output-retention.md)                       |

The descriptor lists each available method exactly. No capability family asserts all-or-nothing support. Optional behavior inside one method is accepted only when the method's request contract and descriptor posture report it truthfully; unsupported options fail before dispatch.

The catalog contains no provider provisioning, container lifecycle, daemon shutdown, credential rotation, arbitrary native PID/path access, URL fetching, product-user authorization, or shell-evaluated administration.

## Descriptor Refresh and Generation

`environment.describe` returns the current session's descriptor. Within one initialized session, runtime policy can only remove `available_methods` and lower numeric `EIPLimits`. Environment identity, generation, mount descriptors and ordering, root mount, shell profiles, isolation posture, and execution-feature support are immutable. A refresh that re-adds a removed method, raises a prior limit, changes topology/posture/features, or contains an unknown method is a terminal protocol violation; a client never replaces its effective descriptor with that observation. A fault that changes those generation-fixed facts drains or terminates the daemon and destroys its sessions.

A daemon restart creates a new unpredictable nonzero generation. Operation records, process handles, transfer handles, output references, receipts, and private spool data from the old generation are invalid and never restored or adopted. A selector that safely identifies another generation returns `stale_generation`; otherwise it returns its non-disclosing invalid/not-found error.

```python
class EnvironmentDescribeParams(BaseModel):
    context: EIPCallContext


class EnvironmentDescribeResult(BaseModel):
    descriptor: EnvironmentDescriptor


class OperationCancelParams(BaseModel):
    context: EIPCallContext
    target_operation_id: str


class OperationCancelResult(BaseModel):
    status: Literal[
        "not_found",
        "already_terminal",
        "cancellation_requested",
        "not_cancellable",
    ]
```

## Opaque Selectors

The base protocol has four opaque selector families:

```python
class ProcessHandle(RootModel[str]): ...
class FileReaderHandle(RootModel[str]): ...
class FileWriterHandle(RootModel[str]): ...
class OutputReference(RootModel[str]): ...
```

Private records bind Environment identity, generation, daemon user, object kind, creating operation, lifecycle, and facts required for safe follow-up. File transfer handles additionally bind the exact initialized session, direction, attachment, next offset, and expiry.

Selectors expose no native PID, path, descriptor, storage key, package SID, or credential. Possession is insufficient: every use repeats carrier trust, method availability, generation, kind, state, and policy checks. Server-created values use concise kind-prefixed generation-local identities where entropy is not a security property. Operation IDs remain unpredictable because they identify replay across reconnects.

Receipts are addressed by operation ID. Retained and process output use client-owned explicit byte offsets against an output reference or process handle; neither read path creates another server selector.

## Binary File-Transfer Control

[Resource Operations](04-resource-operations.md) owns file semantics. EIP control and the binary data plane establish these lifecycles:

### Reader

1. `file.open_reader` authorizes a path and optional byte range and returns a session-owned reader plus observed metadata.
2. The carrier attaches one server-to-client binary stream with exact contiguous offsets and finite bounds.
3. Envd sends `END` after clean producer termination. Readers do not use `END_ACK`.
4. After the public consumer drains all chunks, `file.close_reader` is the sole successful acceptance action. It returns produced-byte count and SHA-256 digest.
5. Early exit, cancellation, reset, expiry, or carrier loss closes the reader without acceptance.

`file.close_reader` has no boolean acceptance choice. It succeeds only for a clean, fully consumed reader. A successful high-level reader compares envd count and digest with locally observed bytes. This proves transfer integrity for the held stream, not an immutable pathname or file snapshot.

### Writer

1. `file.open_writer` authorizes a destination and reserves one bounded destination-local candidate without mutating the target.
2. The carrier attaches one client-to-server stream. Envd writes exact contiguous chunks while counting, hashing, and reserving staging capacity.
3. Client `END` plus envd `END_ACK` seals the uploaded stream but does not publish it.
4. `file.commit_writer` verifies count and digest, atomically hands candidate ownership to the operation, revalidates publication intent, and performs the only destination mutation.
5. `file.abort_writer` or pre-handoff session teardown deletes the candidate when cleanup can be proven.

A data frame is not independently retryable. Interrupted readers open a new explicit observation. Interrupted writers use a new candidate. Only a possibly dispatched commit has mutation ambiguity, reconciled by its operation ID and receipt evidence.

## Operation Ledger and Cancellation

Envd keeps one bounded generation-scoped ledger containing running operations and retained terminal evidence. A running entry can carry a cancellation-requested flag and the latest envd-observed receipt. A retained terminal entry contains exactly one bounded result or typed error plus its final receipt when the method produces one. `unknown_outcome` is a terminal typed error, not another lifecycle state. Completed `active_only` entries are removed rather than becoming terminal replay records.

After typed validation and capacity checks, envd inserts the operation entry synchronously before scheduling handler work. This ordering prevents a later cancellation from overtaking an earlier accepted request. The operation ledger does not own process trees, file transfers, staged candidates, or spool files; their domain records own those resources.

For `terminal_evidence`, terminal publication stores the response/error and receipt atomically. A terminal entry with missing evidence is never visible, and response-waiter or carrier loss neither cancels the operation nor removes its evidence. Running entries are never capacity-reclaimed; retained terminal entries normally follow finite internal record policy. An originating `shell.exec` or `process.start` entry whose result or typed error contains a selector for a still-live process or output record is pinned until every referenced record is explicitly released. Command admission reserves capacity for that one pinned origin entry together with its finite process/output records, so the exception remains bounded by command-resource capacity rather than by the number of later controls. `active_only` response content is never stored in this ledger. Once reclaimable side-effect evidence is gone, absence cannot prove non-dispatch.

Ledger capacity includes a bounded reconciliation reserve that ordinary work and pinned command-origin entries cannot consume. Even when ordinary capacity is full, that reserve fairly admits at least one bounded `operation.cancel`, `receipt.get`, `process.inspect`, `process.kill`, `process.release`, `output.read`, or `output.release` operation at a time. An active reserve entry is never reclaimed. Completed unpinned terminal evidence in the reserve follows ordinary reclamation and can be reclaimed to admit the next reconciliation operation; completed active-only entries leave after response handoff. Existing retained entries remain directly replayable without acquiring another record. Consequently a caller can serially cancel or kill, inspect, read, release the process, and release both outputs until the originating pin becomes reclaimable; ledger saturation cannot make its own cleanup path permanently unreachable.

`operation.cancel` is deliberately `active_only`. It targets an operation ID in the same generation, sets the running entry's cancellation request, and asks the domain owner to stop safely. Acceptance of that request is not terminal proof, and repeating the cancel with a new operation ID can observe `already_terminal` or `not_found`. A `terminal_evidence` target publishes its strongest completed, cancelled, timed-out, or unknown result and retains receipt evidence for later `receipt.get`. Cancellation of an `active_only` target affects only its running entry; once that response handoff ends, no terminal receipt is promised, and a caller that lost the response performs a new observation with a new operation ID. Carrier close never requests cancellation automatically.

Writer commit still has one atomic ownership boundary: either session close retains cleanup responsibility for the staged candidate, or accepted commit owns publication. This domain rule does not require a generic operation ownership framework.

## Receipts and Side-Effect Evidence

A mutating operation can return:

```python
class OperationReceipt(BaseModel):
    operation_id: str
    method: str
    environment_id: str
    generation: int
    request_digest: str
    stage: Literal[
        "accepted",
        "dispatched",
        "exec_confirmed",
        "completed",
        "unknown",
    ]
    outcome: Literal[
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "unknown",
    ] | None
    observed_at: datetime


class ReceiptGetParams(BaseModel):
    context: EIPCallContext
    operation_id: str


class ReceiptGetResult(BaseModel):
    receipt: OperationReceipt
```

A receipt states only facts envd directly observed. `accepted` proves no native dispatch. `dispatched` proves the native boundary was crossed, not completion. `exec_confirmed` proves the requested executable passed the exec handshake. `completed` has a terminal outcome. `unknown` preserves lost certainty.

Receipt evidence is attached to a `terminal_evidence` operation record, has no independent selector or quota, and shares that record's bounded lifetime and reclamation rule, including the command-origin pin while disclosed process/output records remain live. Missing evidence never becomes proof of non-dispatch, mutation failure, Host durability, provider billing, or Agent completion.

## Error Contract

A method error has bounded `error.data`:

```python
type RetryHint = Literal[
    "never",
    "same_request",
    "after_refresh",
    "after_capacity",
    "reconcile_first",
]


type DispatchStage = Literal[
    "pre_dispatch",
    "dispatching",
    "dispatched",
    "completed",
    "unknown",
]


class EIPErrorData(BaseModel):
    error_type: str
    retry_hint: RetryHint
    dispatch_stage: DispatchStage
    operation_id: str | None = None
    environment_id: str | None = None
    generation: int | None = None
    field: str | None = None
    handle_kind: str | None = None
    produced_bytes: int | None = None
    emitted_items: int | None = None
    dropped_items: int | None = None
    process_status: ProcessStatus | None = None
    process: ProcessInfo | None = None
    output: ProcessOutput | None = None
    receipt: OperationReceipt | None = None
    safe_detail: str | None = None
```

Stable code/type pairs are:

|     Code | `error_type`                 | Meaning                                                                    |
| -------: | ---------------------------- | -------------------------------------------------------------------------- |
| `-32700` | `parse_error`                | Invalid JSON before an EIP envelope                                        |
| `-32600` | `invalid_request`            | Invalid JSON-RPC envelope, batch, or notification                          |
| `-32601` | `method_not_found`           | Method absent from selected protocol version                               |
| `-32602` | `invalid_params`             | Typed params fail validation                                               |
| `-32603` | `internal_error`             | Bounded unexpected fault with no narrower class                            |
| `-32001` | `not_initialized`            | Method used before initialization                                          |
| `-32002` | `already_initialized`        | Initialization repeated in one session                                     |
| `-32003` | `protocol_incompatible`      | Version or required-method agreement fails                                 |
| `-32010` | `denied`                     | Configured policy denies the action                                        |
| `-32011` | `not_found_or_denied`        | Object absent or intentionally indistinguishable                           |
| `-32012` | `unsupported`                | Protocol method/option unavailable in current descriptor/posture           |
| `-32020` | `stale_generation`           | Selector belongs to another generation                                     |
| `-32021` | `invalid_handle`             | Handle kind, state, or use is invalid                                      |
| `-32030` | `busy`                       | Bounded admission capacity unavailable                                     |
| `-32031` | `quota_exceeded`             | Finite resource quota cannot reserve capacity                              |
| `-32032` | `output_limit_exceeded`      | A bounded non-command result cannot fit its hard output ceiling            |
| `-32040` | `timeout`                    | Relative deadline expired with known timeout result                        |
| `-32041` | `cancelled`                  | Provider proves cancellation                                               |
| `-32042` | `unknown_outcome`            | Possible effect cannot be classified safely                                |
| `-32043` | `operation_in_progress`      | Same operation ID and digest is active                                     |
| `-32050` | `provider_unavailable`       | Native/provider resource unavailable                                       |
| `-32051` | `execution_isolation_failed` | Required isolation or pre-exec identity failed                             |
| `-32052` | `cleanup_failed`             | Required native cleanup cannot be proven                                   |
| `-32053` | `command_start_failed`       | Selected executable did not execute after preparation                      |
| `-32060` | `conflict`                   | Operation-ID digest, topology, publication, or state precondition conflict |
| `-32061` | `integrity_mismatch`         | Transfer count or SHA-256 evidence differs                                 |

Carrier authentication and upgrade failures happen before JSON-RPC dispatch. Once a valid request is admitted in an initialized session, method failure uses this contract.

The generated codec validates every code/type pair. Counts and status contain bounded evidence, never native identities. After command selectors have been committed, a command terminal error can include `process` for a background owner or `process_status` plus `output` for foreground execution. Those shapes contain only opaque selectors, status, counts, and bounded previews; complete bytes remain solely in the output spool. Clients branch on code and `error_type`, not message text.

## Retry and Unknown Outcomes

A new `active_only` observation uses a new operation ID and is never terminal-replayed. Repeating a retained `terminal_evidence` operation ID with the same method/digest asks for replay of the original result. Mutations can repeat safely only when:

- failure proves `dispatch_stage="pre_dispatch"` and the caller deliberately starts a new operation; or
- the same operation ID, method, and digest still has retained replay evidence; or
- receipt/resource reconciliation proves a terminal fact that makes a new operation safe.

A timeout, cancellation race, carrier close, or response loss after possible dispatch yields `unknown_outcome` unless stronger evidence exists. The client never switches carrier, reconnects, or assigns a new operation ID to repeat an ambiguous mutation automatically.

## Observation Without Push

EIP 1.0 has no application notifications. Clients observe process and output changes through bounded `process.inspect`, `process.wait`, and `output.read`. Explicit offsets and `next_offset` support non-draining reads; each call remains an independently bounded request. `wait_ms` and `timeout_ms` provide bounded long polling where supported.

Binary file data is not state notification. It exists only for one session-owned reader or writer and has no subscription, replay, fan-out, or independent authority.

## Compatibility and Versioning

EIP version is independent of package version, readiness state, and provider profile. Before the first externally supported release, the checked `0.0.0` IDL remains an atomic pre-release snapshot, but every removed field number and name is reserved so stale generated values cannot be reinterpreted accidentally.

Within one supported major:

- additive result fields are compatible when older clients can ignore them safely;
- a new method is compatible only in a negotiated minor and appears explicitly in `available_methods`;
- an optional request field requires a negotiated minor and a non-widening default;
- method names, operation-ID replay scope, timeout meaning, error meaning, selector scope, explicit output offsets, transfer integrity, and writer publication boundary remain stable.

Removing or repurposing a field, changing a method's side-effect boundary, making a selector authoritative, changing replay digest semantics, resuming transfers across sessions, reintroducing reader `END_ACK`, or turning an absolute observation timestamp into a lease requires an incompatible revision.

Common fixtures exercise generated Python against the Rust daemon over stdio, HTTP, and reverse WebSocket. Carrier tests add framing or resource mapping, attachment authentication, reconnect where applicable, liveness, concurrency, streaming, and size cases without redefining protocol results.

## Invariants

01. Every control message contains one correlated JSON-RPC envelope; batches and notifications are invalid, and native file bytes use only the carrier's typed binary-frame or HTTP streaming-body mapping.
02. `initialize` is the first request, verifies Environment identity and exact required methods, and publishes configured mounts, root mount, actionable limits, method availability, generation, and isolation posture.
03. Every later method carries one operation ID; operation ID is the sole active-cancellation identity and, for `terminal_evidence` methods, the sole replay and receipt identity.
04. Same operation ID plus same method/digest reports active progress or replays retained terminal evidence; `active_only` responses are not retained, and another method/digest conflicts while an entry exists.
05. `timeout_ms` is a relative bounded wait/operation budget converted to a monotonic daemon deadline; caller wall-clock timestamps do not govern execution.
06. Exact `available_methods`, not capability families, determines callable support.
07. Opaque selectors grant no authority and none survives daemon restart; file transfer handles are additionally session-scoped.
08. Receipts are addressed only by operation ID and state only envd-observed evidence.
09. Cancellation is a request; only owner evidence establishes terminal cancellation.
10. Reader success requires `file.close_reader` after full consumption and digest verification; readers do not use `END_ACK`.
11. Writer open and upload do not mutate the destination; only integrity-checked commit can publish, and ambiguous commit is reconciled by operation ID.
12. Command output uses stable stdout/stderr references, append-only explicit offsets, and next offsets; there are no output cursor objects or duplicate process-output read method.
13. Errors preserve pre-dispatch, dispatched, completed, and unknown distinctions without exposing secrets or native internals.
14. Carrier choice and reconnect cannot change method, replay, transfer, output, receipt, or compatibility semantics.
