# Python EIP client

`a13n-envd-client` is the low-level Python client for Envd's Environment Interaction Protocol. Use it to implement an Environment Provider or another trusted EIP client. Most agent applications should use [Environment Providers](../a13n-environment/index.md), which already handle target lifecycle and adaptation.

The package does not discover, install, download, or launch Envd. It does not create a reverse-WebSocket listener, issue credentials, retain target state, or run an Agent.

## Install

```console
uv add a13n-envd-client
```

Python 3.13 or later is required. The Python client and native daemon publish at the same Envd release version; EIP's negotiated wire version is a separate identity. Source examples should use the repository lockfile, not a published client against a source-version daemon.

## Connect to an existing HTTP daemon

This complete example requires a running daemon, its expected Environment identity, and a Host-issued credential. It does not provision infrastructure or test account access offline.

```python
import asyncio
import os

from a13n_envd_client import EIPSession, HttpTransport


async def main() -> None:
    transport = HttpTransport(
        endpoint=os.environ["ENVD_ENDPOINT"],
        credential=os.environ["ENVD_CREDENTIAL"],
    )
    session = await EIPSession.initialize(
        transport,
        expected_environment_id=os.environ["ENVD_ENVIRONMENT_ID"],
        required_methods=("environment.describe", "session.close"),
    )
    async with session:
        descriptor = await session.describe()
        print(descriptor.environment_id, descriptor.generation)
        print(descriptor.available_methods)


if __name__ == "__main__":
    asyncio.run(main())
```

The endpoint is the daemon base URL, not `/eip/control`. Initialization includes the first readiness check and closes the transport on failure. Context exit requests a clean EIP session close and releases the transport. No workspace or externally hosted daemon is destroyed.

## Choose a transport

| Transport                                     | Supply                                                         | Ownership and constraints                                                                      |
| --------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `StdioTransport(reader, writer, process=...)` | Parent-owned asyncio streams                                   | Multiplexed EIP control and binary frames over trusted pipes                                   |
| `StdioTransport.from_process(process)`        | An already launched asyncio subprocess with piped stdin/stdout | Launch policy, required runtime directory, stderr draining, and containment belong to the Host |
| `HttpTransport(endpoint, credential, ...)`    | Daemon base URL and attachment credential                      | Authenticated requests and raw transfer streams; client owns its internal HTTP connection pool |
| `AcceptedWebSocketTransport(connection, ...)` | Already accepted and authenticated `WebSocketConnection`       | Requires negotiated `eip.v1`; does not listen, dial, or authenticate the upgrade               |

All three default to 1 MiB each for `max_request_bytes`, `max_response_bytes`, and `max_transfer_frame_bytes`; limits are positive and narrow to the negotiated descriptor. Increasing a client limit cannot grant unsupported server methods.

### HTTP options

| Option                              | Default              | Meaning                                                                                  |
| ----------------------------------- | -------------------- | ---------------------------------------------------------------------------------------- |
| `verify`                            | `True`               | TLS verification; an SSL context or CA path is supported, `False` is rejected            |
| `request_timeout`                   | `30.0` seconds       | Connection/request timeouts; control-read budgets account for operation timeouts         |
| `allow_plaintext_private_link`      | `False`              | Explicit permission for the supported private-link HTTP case; not unrestricted plaintext |
| Request, response, and frame limits | `1048576` bytes each | Carrier-side bounds                                                                      |

The transport does not follow redirects or inherit proxy/environment HTTP settings (`trust_env=False`). `normalize_http_endpoint()` applies the same endpoint policy without opening a connection. See [Remote Envd](../a13n-environment/remote-envd.md) for allowed URL forms, TLS, and credential ownership.

A custom `WebSocketConnection` supplies `subprotocol`, async `send`, `recv`, `close`, and `wait_closed`. The Host validates the credential before handing over the connection. Framework adapters translate disconnects to `EOFError` or `OSError`; `wait_closed` must not compete with the transport's `recv` loop.

## Initialize and maintain a session

`EIPSession.initialize()` accepts:

| Argument                  | Default              | Meaning                                                                     |
| ------------------------- | -------------------- | --------------------------------------------------------------------------- |
| `transport`               | Required             | Fresh EIP carrier                                                           |
| `expected_environment_id` | Required             | Exact trusted target identity, verified against the daemon                  |
| `required_methods`        | `()`                 | Required method names; `environment.readiness` is always added              |
| `client_name`             | `"a13n-envd-client"` | Client descriptor name                                                      |
| `client_version`          | `None`               | Installed distribution version, with source fallback                        |
| `initialization_timeout`  | `10.0` seconds       | Positive finite enclosing deadline for initialization and initial readiness |
| `request_timeout`         | `None`               | Coordinator response allowance; see timeout semantics below                 |
| `max_in_flight`           | `32`                 | Positive concurrency ceiling, narrowed to the descriptor                    |
| `reuse_transport`         | `False`              | Trusted stdio-only reuse after a clean session close                        |

Initialization validates the offered protocol version, target identity, required methods, descriptor topology/features, and initial readiness. Before admission it allows one in-flight operation; after negotiation it applies the effective concurrency and byte limits.

The public session surface is:

- `client`: the generated `EIPClient`, available only while open.
- `descriptor`, `generation`: latest cached descriptor and generation.
- `describe()`: refresh the descriptor. Methods and limits may narrow; identity, generation, topology, shell profiles, execution posture/features, or widening cannot silently change within a session.
- `readiness(timeout=10.0)`: fresh bounded readiness. Failure, invalid correlation, or not-ready terminates the session; false readiness is not a reusable healthy connection.
- `open_reader()`, `open_writer()`, `open_output()`: bounded high-level transfer/observation helpers.
- `close()`: clean close; repeated successful close is harmless. A previously aborted or terminal session cannot claim it closed cleanly.
- `abort()`: close the carrier without claiming the outcome of in-flight work.

A session async context manager closes on exit. If body execution already failed, close errors do not replace that original exception. `reuse_transport=True` detaches the coordinator after a clean close instead of closing the trusted stdio carrier; no other transport supports reuse.

## Read and write binary files

EIP paths contain a mount ID and an absolute **mount-relative** path. They are not Harness aggregate paths or unrestricted native Host paths:

```python
from a13n_envd_client.eip.v1 import EIPPath

path = EIPPath(mount_id="workspace", path="/report.txt")

async with session.open_writer(path, mode="upsert") as writer:
    await writer.write(b"Hello from EIP\n")
    committed = await writer.commit()

async with session.open_reader(path) as reader:
    async for chunk in reader:
        print(chunk.decode("utf-8"), end="")
    completion = reader.completion
```

These fragments require the appropriate advertised methods and writable mount. Use an incremental decoder for arbitrary text streams: chunk boundaries need not coincide with UTF-8 character boundaries. For large transfers, forward bytes to a bounded application sink instead of accumulating all data in memory.

### Reader

`open_reader(path, byte_range=None, transfer_timeout_ms=None)` returns a single-entry `EIPFileReader`. It owns attachment, offset/digest validation, completion evidence, and typed reader close. `opened` exposes the negotiated open result; `open_context` exposes the generated operation context. `completion` is unavailable until terminal evidence exists.

### Writer

`open_writer(path, mode=..., executable=None, transfer_timeout_ms=None)` returns a single-entry staged `EIPFileWriter`. `mode` accepts the generated `FileWriteMode` or its string value. `write()` accepts bytes, bytearray, or memoryview, enforces transfer limits, and splits chunks to the negotiated frame size.

**Context exit does not commit.** Call `await writer.commit()` explicitly; otherwise exit aborts the staged writer. Commit seals the stream, verifies its SHA-256/byte-count evidence, and publishes through the daemon's commit operation. `opened`, `transferred_bytes`, `open_context`, `commit_context`, and the post-commit `result` expose its evidence.

Keep the commit operation ID when recovery may be needed. A timeout after dispatch is not proof of rollback; even abort can report that commit is in progress or already completed.

## Read retained process output

`open_output(reference, start_offset=0, observed=None)` returns an `EIPOutputReader`, not a live process handle. `reference` accepts the generated type or its string value; `observed` can carry a matching prior `OutputInfo`.

```python
reader = session.open_output(output_reference)
while not reader.eof:
    page = await reader.read_page(wait_ms=1000)
    consume_bytes(page.data)
    # Persist offsets only under the Host's own output-retention policy.
```

`output_reference` and `consume_bytes` are application-owned in this fragment. `EIPOutputPage` contains `start_offset`, `next_offset`, `data`, `output`, and `eof`. Reader properties expose `reference`, `offset`, latest `output`, and `eof`. Async iteration yields non-empty byte chunks, waiting in one-second pages until EOF.

The reader validates contiguous offsets, exact reference, monotonic counters and completion, immutable preview prefixes, and terminal byte counts. Invalid evidence closes the coordinator for a protocol error. EOF means the producer completed and the retained end was reached; it does not mean every produced byte was retained. Inspect `OutputInfo.content_complete` and the produced/retained counters before claiming complete output.

## Timeouts, cancellation, and receipts

`RequestCoordinator` correlates bounded request IDs, limits in-flight work, routes binary frames, and surfaces typed errors. It is an advanced transport-integration primitive; normal callers use a session and its generated client.

`EIPCallContext` requires an operation ID of 1–128 characters and optionally a positive uint64 `timeout_ms`. The operation ID is distinct from the JSON-RPC request ID. Supply a stable operation ID when the method's receipt/replay semantics require reconciliation.

With `timeout_ms`, local admission is bounded separately; response waiting permits the operation budget plus the coordinator allowance (30 seconds if no request timeout is configured). Without an operation budget, the configured request timeout applies. A 60-second operation with a 30-second allowance therefore permits a 90-second response wait. Initialization and readiness still have their enclosing deadlines.

Cancellation, a disconnected carrier, or a local timeout does not prove an already sent mutation failed or was absent. The client does not automatically retry ambiguous operations. Reconcile through `receipt.get`, a replay permitted for the method and evidence window, or native-state inspection before issuing a different operation. `operation.cancel` itself does not turn uncertainty into rollback.

## Error reference

| Exception                 | Meaning / evidence                                                   |
| ------------------------- | -------------------------------------------------------------------- |
| `EIPClientError`          | Base client failure                                                  |
| `EIPProtocolError`        | Invalid framing, correlation, or protocol evidence                   |
| `EIPTransportError`       | Transport failed before a valid correlated response                  |
| `EIPTransportClosedError` | Closed carrier, potentially with in-flight requests                  |
| `EIPConnectionError`      | Connection establishment/exchange failure, not proof of non-dispatch |
| `EIPRequestTimeoutError`  | Local wait expired; inspect `dispatched`                             |
| `EIPMethodError`          | Valid correlated EIP error; inspect typed `error`                    |
| `EIPSessionStateError`    | Invalid local session/helper state or unavailable method             |
| `EIPTransferError`        | Transfer reset/failure; optional `status` and `offset` evidence      |

Custom carrier integrations implement `EIPTransport` and exchange `ControlFrame` or generated binary `DataFrame` values through `EIPTransportFrame`. They must preserve framing, size limits, serialization, and lifecycle; these exports are not another provisioner API.

## Generated method reference

The current generated surface below comes from `a13n_envd_client.eip.v1.METHODS`. Import parameter/result types, enums, codecs, `EIP_PROTOCOL_VERSION`, and the low-level `EIPClient` from that module. The IDL and generator own the wire schema; do not hand-edit generated files.

Availability remains the initialized descriptor's decision. A generated method existing in Python does not mean every configured daemon exposes it.

| EIP method              | Python method           | Parameters                   | Result                       | Replay class        |
| ----------------------- | ----------------------- | ---------------------------- | ---------------------------- | ------------------- |
| `environment.describe`  | `environment_describe`  | `EnvironmentDescribeParams`  | `EnvironmentDescribeResult`  | `active_only`       |
| `environment.readiness` | `environment_readiness` | `EnvironmentReadinessParams` | `EnvironmentReadinessResult` | `active_only`       |
| `file.abort_writer`     | `file_abort_writer`     | `FileWriterAbortParams`      | `FileWriterAbortResult`      | `active_only`       |
| `file.close_reader`     | `file_close_reader`     | `FileReaderCloseParams`      | `FileReaderCloseResult`      | `active_only`       |
| `file.commit_writer`    | `file_commit_writer`    | `FileWriterCommitParams`     | `FileWriterCommitResult`     | `terminal_evidence` |
| `file.copy`             | `file_copy`             | `FileCopyParams`             | `FileCopyResult`             | `terminal_evidence` |
| `file.find`             | `file_find`             | `FileFindParams`             | `FileFindResult`             | `active_only`       |
| `file.list`             | `file_list`             | `FileListParams`             | `FileListResult`             | `active_only`       |
| `file.mkdir`            | `file_mkdir`            | `FileMkdirParams`            | `FileMkdirResult`            | `terminal_evidence` |
| `file.move`             | `file_move`             | `FileMoveParams`             | `FileMoveResult`             | `terminal_evidence` |
| `file.open_reader`      | `file_open_reader`      | `FileReaderOpenParams`       | `FileReaderOpenResult`       | `active_only`       |
| `file.open_writer`      | `file_open_writer`      | `FileWriterOpenParams`       | `FileWriterOpenResult`       | `active_only`       |
| `file.patch_text`       | `file_patch_text`       | `FilePatchTextParams`        | `FilePatchTextResult`        | `terminal_evidence` |
| `file.read_text`        | `file_read_text`        | `FileReadTextParams`         | `FileReadTextResult`         | `active_only`       |
| `file.remove`           | `file_remove`           | `FileRemoveParams`           | `FileRemoveResult`           | `terminal_evidence` |
| `file.search`           | `file_search`           | `FileSearchParams`           | `FileSearchResult`           | `active_only`       |
| `file.stat`             | `file_stat`             | `FileStatParams`             | `FileStatResult`             | `active_only`       |
| `file.write_text`       | `file_write_text`       | `FileWriteTextParams`        | `FileWriteTextResult`        | `terminal_evidence` |
| `initialize`            | `initialize`            | `InitializeParams`           | `InitializeResult`           | `ledger_external`   |
| `operation.cancel`      | `operation_cancel`      | `OperationCancelParams`      | `OperationCancelResult`      | `active_only`       |
| `output.read`           | `output_read`           | `OutputReadParams`           | `OutputReadResult`           | `active_only`       |
| `output.release`        | `output_release`        | `OutputReleaseParams`        | `OutputReleaseResult`        | `terminal_evidence` |
| `port.inspect`          | `port_inspect`          | `PortInspectParams`          | `PortInspectResult`          | `active_only`       |
| `port.wait`             | `port_wait`             | `PortWaitParams`             | `PortWaitResult`             | `active_only`       |
| `process.close_stdin`   | `process_close_stdin`   | `ProcessCloseStdinParams`    | `ProcessCloseStdinResult`    | `terminal_evidence` |
| `process.inspect`       | `process_inspect`       | `ProcessInspectParams`       | `ProcessInspectResult`       | `active_only`       |
| `process.kill`          | `process_kill`          | `ProcessKillParams`          | `ProcessKillResult`          | `terminal_evidence` |
| `process.release`       | `process_release`       | `ProcessReleaseParams`       | `ProcessReleaseResult`       | `terminal_evidence` |
| `process.signal`        | `process_signal`        | `ProcessSignalParams`        | `ProcessSignalResult`        | `terminal_evidence` |
| `process.start`         | `process_start`         | `ProcessStartParams`         | `ProcessStartResult`         | `terminal_evidence` |
| `process.wait`          | `process_wait`          | `ProcessWaitParams`          | `ProcessWaitResult`          | `active_only`       |
| `process.write_stdin`   | `process_write_stdin`   | `ProcessWriteStdinParams`    | `ProcessWriteStdinResult`    | `terminal_evidence` |
| `receipt.get`           | `receipt_get`           | `ReceiptGetParams`           | `ReceiptGetResult`           | `active_only`       |
| `session.close`         | `session_close`         | `SessionCloseParams`         | `SessionCloseResult`         | `active_only`       |
| `shell.exec`            | `shell_exec`            | `ShellExecParams`            | `ShellExecResult`            | `terminal_evidence` |

Inspect the exact versioned fields and validation constraints when building requests:

```python
from a13n_envd_client.eip.v1 import FileReadTextParams, METHODS

schema = FileReadTextParams.model_json_schema()
assert "context" in schema["properties"]
assert "file.read_text" in METHODS
```

The [EIP contract](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd) defines operation outcomes, receipt windows, transfer integrity, and compatibility. The generated method metadata records the replay class; it is not permission to repeat an uncertain mutation with a new operation ID.

## Validate and choose a higher-level API

```console
uv run --locked pytest packages/a13n-envd-client/tests
```

The client suite covers framing, sessions, errors, transfers, and output with protocol fixtures. Actual OS isolation and daemon availability need the separate Envd integration checks. For Host-owned process launch/runtime bootstrap, use [Local Envd](index.md#recommended-harness-path); for application tools, use [Environment operations](../a13n-environment/operations.md).
