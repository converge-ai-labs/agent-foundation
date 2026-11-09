# Environment Interaction Protocol

## Design Position

EIP 0.5 separates a Device connection from the independent Sessions it carries. JSON-RPC 2.0 carries control; typed transfers carry raw file bytes. HTTP, reverse WebSocket and stdio share method, ownership and cleanup semantics. Provider provisioning and product authorization remain outside EIP.

The [canonical IDL](08-protocol-source-client-and-generation.md) generates Rust/Python wire types and fixtures.

## Control Envelope

Each message is one JSON-RPC request/response, without batches or application notifications. IDs are strings or signed 64-bit integers, never booleans. A response echoes its request ID and contains exactly one result/error. Session-scoped messages carry and echo `eip_session`; requests also carry `eip_owner_epoch` to fence obsolete owners; `initialize`, `device.describe`, `device.metrics`, `directory.list` and `session.open` omit it. This field is a route selector, not a credential.

Framing, JSON sizes and typed values are bounded. Validate required types, method availability, route ownership and native operation inputs at their owning boundary. There is no additional per-resource authorization lattice. Small inherent bytes such as stdin use `EncodedBytes(encoding="base64", data=...)` with canonical unpadded base64. Native file content uses the raw data plane.

## Initialization

These conceptual shapes are realized by the IDL:

```python
class InitializeParams(BaseModel):
    client: EIPClientInfo
    expected_device_id: str | None


class DeviceDescriptor(BaseModel):
    device_id: str
    generation: int
    display_name: str | None
    description: str | None
    path_style: Literal["posix", "windows"]
    default_working_directory: str
    directory_discovery: bool
    boundary: ExecutionBoundary
    available_methods: tuple[str, ...]
    limits: EIPLimits
    lifecycle: SessionLifecyclePolicy
    system_info: SystemInfo | None


class InitializeResult(BaseModel):
    server: EIPServerInfo
    descriptor: DeviceDescriptor


class SessionConfiguration(BaseModel):
    working_directory: str | None = None
    sandbox: SandboxPolicy | None = None
    egress_mode: EgressMode | None = None
    egress: EgressPolicy | None = None
    lifecycle: SessionLifecyclePolicy | None = None
    commands: bool | None = None
    isolation: SessionIsolation | None = None


class SessionOpenParams(BaseModel):
    expected_device_id: str
    expected_generation: int
    required_methods: tuple[str, ...]
    configuration: SessionConfiguration


class SessionDescriptor(BaseModel):
    boundary: ExecutionBoundary
    egress: EgressStatus | None = None
    device_id: str
    generation: int
    session_id: str
    owner_epoch: int
    path_style: Literal["posix", "windows"]
    system_info: SystemInfo
    working_directory: str
    available_methods: tuple[str, ...]
    limits: EIPLimits
    shell_profiles: tuple[ShellProfileDescriptor, ...]
    execution_features: ExecutionFeatures
    lifecycle: SessionLifecyclePolicy


class SessionOpenResult(BaseModel):
    descriptor: SessionDescriptor
```

`initialize` verifies the Device and returns its descriptor without creating a Session. Initialization and Session open carry no protocol version or version negotiation. A null expected identity is for explicit trusted registration only. An unexpected Device ID returns `protocol_incompatible` (-32003) with `data.safe_detail` set to `device_id_mismatch` and the message `Check the environment and use its registered Device ID with --device-id.` `device.describe` returns the current Device descriptor, including basic system information. Optional `device.metrics` reads a cached Device sample as defined by [resource monitoring](11-resource-monitoring.md). `directory.list` provides optional [directory discovery](04-resource-operations.md#directory-discovery) without a Session.

`session.open` requires a `configuration` object on the wire, checks the observed identity/generation and required methods, resolves its working directory and reserves one Session. Omitted/null `configuration.working_directory` selects the advertised Device default for native execution; an explicit path overrides it. VM working directories address the guest filesystem. Missing or inaccessible directories fail without creation or fallback. The result contains the exact resolved working directory, immutable for that Session. A working directory is not a filesystem access boundary.

The Host selects each Session's configuration within the launch authority ceiling. Cwd validation, commands and file operations use that Session's grants and identity; Device directory discovery uses launch authority. Device and Session descriptors expose their respective `boundary`; Session publication follows successful worker preparation. Controlled Sessions require explicit `configuration.egress.destinations` on Session open; inherit and deny reject Session policy. The [execution contract](07-execution-isolation.md) owns the tagged policies and descriptor digest. `required_methods` is a compatibility assertion, not a grant. Host/Harness tool policy controls what the model may invoke. Limits describe actual finite operation capacity, not a separate security policy.

Device methods and Session lifecycle controls are bounded and ledger-external. An uncertain open is not automatically retried; an unclaimed Session expires. Changing the Session default requires a new Session. Per-command cwd overrides do not mutate it. Host Run acceptance captures an explicit directory before execution; retries and recovery never reselect a mutable Device default.

A Session descriptor reports the execution backend's path style, system information, resolved cwd, methods, shell profiles and execution features after preparation. Consumers use these Session facts for file and command semantics. Device information describes the outer launcher. The launcher owns the effective isolation boundary and Session identity; worker readiness supplies execution facts, not authority to attest its own isolation.

## Readiness and Session Control

Before initial readiness, a Session admits readiness, keepalive and close only. `environment.readiness` returns `ready`, `device_id`, `generation` and `session_id`; the requester checks them before publishing operations. `environment.describe` returns the Session descriptor. Readiness proves the current operation scope works, not future capacity or sandbox enforcement.

`session.keepalive` maintains the owning execution context during quiet periods. `session.attach` takes over that same Host's existing Session with a strictly newer owner epoch on HTTP or an initialized framed carrier. It returns the original working directory, fixed boundary, lifecycle policy and current nonsecret policy descriptor. `session.detach` releases the attachment and starts the idle window from final use while preserving commands, output and evidence. `session.close` begins cleanup of that Session only. Once cleanup starts, attach/keepalive fail rather than revive it.

[Resource Lifetime](09-resource-lifetime-and-reclamation.md) owns inactivity, maximum lifetime, ownership transfer and collection. No cross-Session resource import, resource-retain method or command lease exists. A client never sends keepalive for a scope whose owner has ended.

## Common Operation Context and Replay

Ordinary Session operations carry:

```python
class EIPCallContext(BaseModel):
    operation_id: str
    timeout_ms: int | None = None


class OperationReference(BaseModel):
    device_id: str
    generation: int
    session_id: str
    operation_id: str
```

Operation identity includes its Session. The client supplies a fresh bounded unpredictable operation ID. Cancellation, receipt lookup and replay address only the current Session; an ID cannot retarget work into another Session.

Methods are `terminal_evidence` or `active_only`. Mutations and command controls retain bounded terminal results/errors/receipts. Observations track active duplicates/cancellation but do not retain every response page. The canonical digest includes protocol, method and typed params, excluding operation ID and relative timeout. Session identity fixes the default working directory. Native operands remain part of each operation's digest.

| Existing operation | Request                 | Result                                         |
| ------------------ | ----------------------- | ---------------------------------------------- |
| None               | New valid request       | Reserve and register before dispatch           |
| Running            | Same method/digest      | `operation_in_progress`; no duplicate dispatch |
| Retained terminal  | Same method/digest      | Replay retained result/error                   |
| Retained ID        | Different method/digest | `conflict`; no dispatch                        |

Response handoff and evidence publication are bounded and atomic at the operation owner. Replay does not restore released/collected resources. Clients do not retry ambiguous mutations merely because evidence was collected or a new Session exists.

`timeout_ms` is a daemon-monotonic operation deadline narrowed by method limits. A pre-dispatch timeout proves no dispatch; later timeout requests cancellation or reconciliation, not rollback. A local wait timeout alone does not terminate a remote command.

## Method Availability and Catalog

| Domain                      | Methods                                                                                                                                                  | Replay class                        |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Device                      | `initialize`, `device.describe`, `device.metrics`, `directory.list`, `session.open`                                                                      | Ledger-external                     |
| Session lifecycle           | `session.attach`, `session.detach`, `session.keepalive`, `session.close`                                                                                 | Ledger-external                     |
| Session observation         | `environment.readiness`, `environment.describe`                                                                                                          | `active_only`                       |
| Evidence/control            | `operation.cancel`, `receipt.get`                                                                                                                        | `active_only`                       |
| File observations/transfers | `file.stat`, `file.read_text`, `file.open_reader`, `file.close_reader`, `file.list`, `file.find`, `file.search`, `file.open_writer`, `file.abort_writer` | `active_only`                       |
| File mutations              | `file.write_text`, `file.commit_writer`, `file.mkdir`, `file.patch_text`, `file.copy`, `file.move`, `file.remove`                                        | `terminal_evidence`                 |
| Foreground command          | `shell.exec`                                                                                                                                             | `terminal_evidence`                 |
| Background observations     | `process.resolve_start`, `process.inspect`, `process.wait`                                                                                               | `active_only`                       |
| Background controls         | `process.start`, `process.write_stdin`, `process.close_stdin`, `process.signal`, `process.kill`, `process.release`                                       | `terminal_evidence`                 |
| Ports                       | `port.inspect`, `port.wait`                                                                                                                              | `active_only`                       |
| Output                      | `output.read` / `output.release`                                                                                                                         | `active_only` / `terminal_evidence` |

[Computer use](10-computer-use.md) adds active-only `computer.describe`, `computer.observe`, `computer.zoom`, `computer.close_observation`, and `computer.read_clipboard`, plus terminal-evidence `computer.click`, `computer.move`, `computer.drag`, `computer.scroll`, `computer.type_text`, `computer.press_keys`, and `computer.write_clipboard`. Screenshot bytes use the same raw transfer plane as file readers. EIP 0.5 also defines seven active-only `computer.a11y_*` reads and 81 independent terminal-evidence AX/UIA/AT-SPI mutations in the canonical accessibility IDL. Their native ownership, bounded reads and uncertain-dispatch semantics belong to [native accessibility](10-computer-use.md#native-accessibility).

Unknown methods return `method_not_found`; known unavailable methods return `unsupported` before dispatch. Exact available methods determine support. `ExecutionFeatures` advertises optional native resource limits and signals, not per-command sandbox/network policy.

## Opaque Selectors

ProcessHandle and OutputReference identify records owned by one Session in one Device generation. FileReaderHandle and FileWriterHandle additionally bind to their current transfer attachment. Opaque IDs expose no PID, path or storage key. Every use resolves through the selected Session; sibling handles are invalid even when both Sessions use the same working directory.

Same-Session reattachment preserves retained process/output/evidence references, not incomplete transfer handles or pending response delivery. Session cleanup and daemon restart invalidate them. Host process references and durable tool-execution IDs remain separate projections with Host-defined lifetimes.

## File Transfer Semantics

Reader open reserves a Session reader for a path/range. Producer END is not acceptance: successful `file.close_reader` requires full consumer drain and byte-count/SHA-256 verification. Readers have no END_ACK. Early reset/close/expiry cannot report a complete download.

Writer open creates a destination-local candidate without changing the destination. Contiguous chunks are counted and hashed; END_ACK seals the upload. Commit takes ownership of the candidate and frozen destination before publication. Session cleanup aborts only pre-handoff candidates; an accepted commit settles with strongest observed evidence. Reattachment does not resume uploads/downloads.

## Operation Ledger and Cancellation

Each Session owns its bounded operation ledger; aggregate limits cover all ledgers. Running operations are not evicted. Evidence for live command resources stays with them; completed history is eligible for finite or pressure-driven collection. Missing evidence is not proof of non-dispatch.

`process.resolve_start` observes a prior `process.start` by `target_operation_id` in the same Session. It never dispatches a command. A retained successful start returns its original process handle and receipt with the current process status. An active or completing start returns `operation_in_progress`; a retained failure returns its original error. A missing start returns `unknown_outcome`, and a target belonging to another method returns `conflict`. Missing evidence or a released process cannot justify replay. The query has its own fresh operation ID and uses active-only retention.

Lifecycle, cancellation and release must still make progress when ordinary admission is full. Use the existing bounded control path rather than introducing a separate recovery subsystem. `operation.cancel` returns not_found/already_terminal/cancellation_requested/not_cancellable and never equates an acknowledgement with completed cleanup.

## Receipts and Errors

OperationReceipt contains operation identity, method, request digest, observed timestamp, stage and optional outcome. Stages are accepted/dispatched/exec_confirmed/completed/unknown; outcomes are succeeded/failed/cancelled/timed_out/unknown. These are native observations, not durable Agent completion.

Errors carry a bounded type, retry hint, dispatch stage and optional operation/command/output evidence. They exclude secrets, native paths/PIDs and complete output. Retry hints are never/same_request/after_refresh/after_capacity/reconcile_first. Dispatch stages are pre_dispatch/dispatching/dispatched/completed/unknown. Generated codecs own exact code/type pairs.

|   Code | Type                    |
| -----: | ----------------------- |
| -32700 | `parse_error`           |
| -32600 | `invalid_request`       |
| -32601 | `method_not_found`      |
| -32602 | `invalid_params`        |
| -32603 | `internal_error`        |
| -32001 | `not_initialized`       |
| -32002 | `already_initialized`   |
| -32003 | `protocol_incompatible` |
| -32010 | `denied`                |
| -32011 | `not_found_or_denied`   |
| -32012 | `unsupported`           |
| -32020 | `stale_generation`      |
| -32021 | `invalid_handle`        |
| -32022 | `session_expired`       |
| -32030 | `busy`                  |
| -32031 | `quota_exceeded`        |
| -32032 | `output_limit_exceeded` |
| -32040 | `timeout`               |
| -32041 | `cancelled`             |
| -32042 | `unknown_outcome`       |
| -32043 | `operation_in_progress` |
| -32050 | `provider_unavailable`  |
| -32052 | `cleanup_failed`        |
| -32053 | `command_start_failed`  |
| -32060 | `conflict`              |
| -32061 | `integrity_mismatch`    |

Expired/collected selectors return invalid/not-found or Session-expired errors; permanent tombstones are unnecessary. An error after command ownership commits preserves available opaque resource references and bounded evidence while that Session exists.

## Retry, Observation and Compatibility

The client never automatically replays possibly dispatched work after carrier loss, recreates it in another Session or chooses another operation ID to hide ambiguity. Exact retained same-Session replay is the only replay facility. Process inspect/wait, receipt lookup and output reads provide explicit bounded observation.

EIP uses binary profile 1, Session addressing and owner epochs. Peers do not negotiate or compare EIP versions. Cross-release interoperability is best effort through the actual schemas and advertised methods; there are no legacy protocol branches or downgrade retries. Invalid fields, unavailable required methods and unsupported binary frame profiles fail at their ordinary decoding or admission boundary.

`egress.update` is the ledger-external Session control for atomic policy and secret updates. The [egress contract](07-execution-isolation.md#controlled-session-egress) owns revision, injection and isolation semantics; generated IDL owns its wire fields.
