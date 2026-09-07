# Command Output Spool

## Design Position

`a13n-envd` continuously drains command stdout and stderr into separate append-only files in its generation-private spool. Every `shell.exec` result and every background process exposes both streams through opaque references and explicit byte offsets. A bounded prefix preview is returned for convenience, but it is never the retention authority.

This design follows normal logging and recording programs rather than terminal scrollback. Kernel pipes and PTYs are bounded flow-control queues: unread writers eventually block, and bytes disappear from the kernel once read. A terminal emulator drains a PTY into a screen and usually a bounded user-space scrollback buffer, which is rendered terminal state rather than an exact stdout/stderr archive. Envd instead behaves like a log collector: it drains the command's separate stdout/stderr pipes promptly and appends their raw bytes to disk. It performs no terminal emulation, control-sequence interpretation, or rendered-screen history.

Output is finite. Before a command can start, envd reserves one configured byte allowance for stdout, another equal allowance for stderr, and the required output records under the daemon-wide spool disk ceiling. Each stream is retained completely while it stays within its allowance. Crossing either ceiling terminates the command and makes incompleteness explicit; it never turns a partial capture into a successful complete result.

## Boundaries

| Concern                                                                 | Owner                                                                | Contract                                             |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------- |
| Raw stdout/stderr draining, spool files, references, reads, and release | `a13n-envd`                                                          | This document                                        |
| Command status and output-limit termination                             | [Command and Process Execution](05-command-and-process-execution.md) | Associates two streams with one command              |
| Model-visible size, redaction, truncation, and logical references       | Harness                                                              | Applied after bounded provider output exists         |
| Native file download and upload                                         | [Resource Operations](04-resource-operations.md)                     | Uses the binary transfer plane, not the output spool |
| Durable artifacts or checkpoints                                        | Host                                                                 | Never implied by an output reference                 |

Envd does not accept a per-request fail/truncate/retain policy. Its job is to preserve bounded raw command output. A consumer decides how much of that output to display, return to a model, or copy into durable storage.

## Output Model

The following shapes are serialized EIP JSON:

`OutputReference` is the generation-scoped opaque selector defined by [EIP Protocol](02-eip-protocol.md#opaque-selectors).

```python
class OutputInfo(BaseModel):
    reference: OutputReference
    producer_complete: bool
    content_complete: bool
    produced_bytes: int
    retained_bytes: int
    preview: EncodedBytes


class OutputReadParams(BaseModel):
    context: EIPCallContext
    reference: OutputReference
    start_offset: int
    wait_ms: int = 0


class OutputReadResult(BaseModel):
    start_offset: int
    data: EncodedBytes
    next_offset: int
    output: OutputInfo


class OutputReleaseParams(BaseModel):
    context: EIPCallContext
    reference: OutputReference


class OutputReleaseResult(BaseModel):
    released: bool
    receipt: OperationReceipt
```

Counts refer to raw bytes before base64 and JSON framing. `preview` is the stream prefix up to the descriptor's finite preview limit. It can equal the complete stream when small, but clients use `content_complete` and `producer_complete`, not preview length, to determine completeness.

`produced_bytes` counts bytes envd observed from the stream. `retained_bytes` is the append-only readable length. `content_complete=true` means every observed byte is retained; `producer_complete=true` means no more bytes can arrive. A normally completed command whose stdout and stderr each stayed within the per-stream ceiling has both flags true for both streams and `produced_bytes == retained_bytes`.

Each reference has one logical range, always `[0, retained_bytes)`. Envd does not rotate a live object, discard its head, create middle gaps, or expose server-side cursors. Stdout and stderr have independent references and offset domains; EIP does not invent a deterministic merged ordering between them.

## Production and Capacity

Before payload release, envd reserves:

- two output records, one for stdout and one for stderr;
- one complete configured byte allowance for each stream under the daemon-wide spool ceiling; and
- bounded preview and bookkeeping memory.

This is a logical capacity guarantee, not a promise that the filesystem cannot fail. It prevents another command from consuming capacity already needed to retain this command's output. When both producers become terminal, envd releases the unused portion and keeps only actual retained bytes charged until output release. Daemon-wide spool bytes and records remain finite. When capacity is unavailable, command admission fails before the payload starts; envd does not evict an existing reference.

After start, stdout and stderr are drained concurrently. Raw bytes append to their respective private spool files, while only each bounded prefix preview and small bookkeeping remain in memory. Normal pipe backpressure therefore depends on drain throughput rather than on a large in-memory result buffer.

`EnvironmentDescriptor.limits.max_output_bytes_per_stream` is the maximum bytes retained independently for stdout and stderr. `max_output_preview_bytes` bounds each returned preview. A finite daemon-wide spool disk ceiling bounds aggregate retained data across commands. If either stream ceiling is crossed, envd:

1. preserves the retained prefixes already written;
2. requests strongest command-tree termination;
3. continues draining and counting discarded bytes so retention does not intentionally leave either pipe full; and
4. reports `termination_reason="output_limit"` with `content_complete=false` for any affected stream.

The method still returns the command's typed terminal status and output references. It does not report a normal complete command or hide side effects that happened before termination.

A spool write failure after payload start follows the same safe drain and cleanup path, but reports a provider or cleanup failure with the strongest known command and receipt evidence.

## Reads

`output.read` returns at most one contiguous response-bounded page beginning exactly at `start_offset`. `next_offset` equals `start_offset + len(data)` in raw bytes. A sequential reader starts at zero and supplies a fresh operation ID for each later page.

- If `start_offset < retained_bytes`, the response returns available bytes up to the response ceiling.
- If `start_offset == retained_bytes`, the response is empty unless bounded `wait_ms` observes more bytes first.
- If the producer completes while waiting, the response can be empty with `producer_complete=true`.
- If `start_offset > retained_bytes`, the request is invalid; envd never skips forward or fabricates zero-filled data.

`wait_ms` is a non-negative long-poll bound narrowed by the call's relative timeout and daemon ceiling. Reads are non-draining: several clients can read the same reference independently, and reading does not change retention lifetime.

## Lifetime and Release

Output belongs to the daemon generation, not the protocol session. Carrier loss and `session.close` do not release it. A new initialized session for the same Environment and generation can continue reading from any valid offset.

A reference remains valid until one of these explicit boundaries:

- `output.release` succeeds after its producer is complete and, for a background stream, after `process.release` has detached it; or
- the daemon generation ends.

`process.release` removes only the terminal process record. It atomically detaches both output objects but leaves their references, bytes, and quota charges intact, so stdout and stderr can then be released independently without a two-file deletion transaction.

There is no idle expiry or capacity eviction of a valid output object. A caller that keeps references consumes its finite spool allocation and can cause later command admission to fail. This makes retention predictable and keeps reclamation explicit.

Releasing an active stream or one still attached to a process record is a conflict. Release is idempotent while its bounded terminal operation evidence remains. Logical invalidation and physical deletion of that one spool object complete as one reclaim action; if envd cannot prove deletion, it returns `cleanup_failed`, keeps the capacity charged, and does not report successful release. Release reclaims the spool object; it cannot revoke bytes already delivered to a client and is not secure erasure of the bounded preview retained in an earlier terminal command result.

No output reference survives daemon restart, becomes Harness continuation state, or names a native path. Before a restarted daemon becomes locally ready, it follows the [runtime-parent lock and verified stale-generation cleanup contract](01-daemon-lifecycle-and-configuration.md#generation-private-runtime-state), so crash-left spool files cannot escape aggregate disk boundedness merely because their selectors became stale. A Host that needs longer or durable retention copies the bytes before release through its own artifact contract.

## Storage and Security

Spool files live under the fresh generation-private runtime subtree. They are outside configured EIP mounts and required-isolation command grants, including when an intentionally broad mount is configured. Resource path enforcement and command isolation each protect that boundary independently. Native permissions or ACLs are defense in depth.

References reveal no path or storage key and grant no authority by possession. Each read and release repeats session trust, generation, kind, method availability, and lifecycle checks. Output content never enters normal logs, metrics, traces, or receipts. A terminal command error emitted after ownership commit can carry only the same bounded prefix previews and opaque references allowed in a successful command result; complete output remains solely in the spool.

At-rest encryption is provider storage policy. Envd does not add a second storage adapter or encryption abstraction to EIP.

## Failure Semantics

| Failure                                     | Observable result                                                     | Guarantee                                                |
| ------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------- |
| Spool records or reserved bytes unavailable | `busy` or `quota_exceeded` before payload release                     | No command starts and no valid reference is evicted      |
| Either per-stream output ceiling is crossed | Command terminates with `output_limit`; affected output is incomplete | Retained prefix remains readable and pipes keep draining |
| Spool write fails after start               | Provider/cleanup failure plus command and receipt evidence            | No false completeness or hidden dispatch                 |
| Offset exceeds current retained length      | `invalid_params`                                                      | No skipped or fabricated bytes                           |
| Reference released or unknown               | `invalid_handle` or `not_found_or_denied`                             | No silent retargeting                                    |
| Reference belongs to another generation     | `stale_generation`                                                    | No cross-generation adoption                             |
| Physical deletion cannot be proven          | `cleanup_failed`; capacity stays charged                              | No quota undercount                                      |

## Compatibility

Raw-byte counting, separate stdout/stderr references, append-only range semantics, explicit offsets, next offsets, completion flags, output-limit behavior, and generation lifetime are EIP compatibility facts. Changing a reference into rendered terminal history, a consuming cursor, a rotating ring, or a durable artifact requires an incompatible protocol revision.

## Invariants

1. Envd installs separate stdout and stderr drainers before payload release and continuously appends each stream to its generation-private spool object while it remains within the ceiling.
2. A successfully completed command whose stdout and stderr each stay within `max_output_bytes_per_stream` retains every byte from both streams.
3. Only bounded previews and bookkeeping remain in memory; complete large output is stored in disk-backed spool files.
4. Each output has one readable range `[0, retained_bytes)` and explicit client-owned offsets; there are no rings, gaps, or output cursor objects.
5. Crossing the hard output ceiling terminates the command and reports incompleteness rather than returning a false complete result.
6. Existing references are never evicted to admit another command.
7. Output survives session reconnect only within the same daemon generation and remains until explicit release or generation end.
8. Spool paths and content never enter EIP selectors, command authority, normal observability, or portable Harness state.
