---
title: Python EIP client
description: The low-level Python client for implementing Environment Providers and other trusted EIP clients.
---

The client ships as `a13n-envd-client`. Most agent applications should use [Environment Providers](../environments/index.md) instead; they already handle target lifecycle and adaptation.

The package does not discover, install, download, or launch Envd. It does not create a reverse-WebSocket listener, issue credentials, retain target state, or run an Agent.

## Install

```console
uv add a13n-envd-client
```

Python 3.13 or later is required. The Python client and native daemon publish at the same Envd release version; EIP's negotiated wire version is a separate identity. Source examples should use the repository lockfile, not a published client against a source-version daemon.

## Connect to an existing HTTP daemon

This complete example requires a running daemon, its expected Device identity, and a Host-issued credential. It does not provision infrastructure or test account access offline.

```python
import asyncio
import os

from a13n_envd_client import EIPDeviceConnection, HttpTransport


async def main() -> None:
    transport = HttpTransport(
        endpoint=os.environ["ENVD_ENDPOINT"],
        credential=os.environ["ENVD_CREDENTIAL"],
    )
    device = await EIPDeviceConnection.initialize(
        transport, expected_device_id=os.environ["ENVD_DEVICE_ID"],
    )
    async with device:
        info = await device.describe()  # Discovery does not open a Session.
        print(info.device_id, info.default_working_directory)
        async with await device.open_session(
            working_directory=info.default_working_directory,
            required_methods=("file.read_text",),
        ) as session:
            print(session.session_id, session.descriptor.working_directory)



if __name__ == "__main__":
    asyncio.run(main())
```

The endpoint is the daemon base URL, not `/eip/control`. Device initialization negotiates the protocol and verifies identity without opening a Session. `open_session()` creates an independent fixed-cwd scope and checks readiness. Session context exit closes only that Session. Device context exit closes its Sessions and physical transport, not the external daemon or the files on the Device.

## Choose a transport

| Transport                                     | Supply                                                         | Ownership and constraints                                                                      |
| --------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `StdioTransport(reader, writer, process=...)` | Parent-owned asyncio streams                                   | Multiplexed EIP control and binary frames over trusted pipes                                   |
| `StdioTransport.from_process(process)`        | An already launched asyncio subprocess with piped stdin/stdout | Launch policy, required runtime directory, stderr draining, and containment belong to the Host |
| `HttpTransport(endpoint, credential, ...)`    | Daemon base URL and attachment credential                      | Authenticated requests and raw transfer streams; client owns its internal HTTP connection pool |
| `AcceptedWebSocketTransport(connection, ...)` | Already accepted and authenticated `WebSocketConnection`       | Requires negotiated `eip.v1`; does not listen, dial, or authenticate the upgrade               |

All transports default to 1 MiB each for `max_request_bytes`, `max_response_bytes`, and `max_transfer_frame_bytes`; limits are positive and narrow to the negotiated descriptor. Increasing a client limit cannot grant unsupported server methods.

### HTTP options

| Option                              | Default              | Meaning                                                                                                  |
| ----------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------- |
| `verify`                            | `True`               | TLS verification; supports SSL context or CA path; explicit `False` disables certificate/hostname checks |
| `request_timeout`                   | `30.0` seconds       | Connection/request timeouts; control-read budgets account for operation timeouts                         |
| `allow_plaintext_private_link`      | `False`              | Explicit permission for the supported private-link HTTP case; not unrestricted plaintext                 |
| Request, response, and frame limits | `1048576` bytes each | Carrier-side bounds                                                                                      |

The transport does not follow redirects. HTTPS attachments honor `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` and their lowercase forms through `httpx2`; the deployment operator's proxy is trusted to route the connection. Plaintext loopback and provider-private-link attachments stay direct so their credential does not leave the intended link. TLS verification still uses `verify`, not `SSL_CERT_FILE` or `SSL_CERT_DIR`. `normalize_http_endpoint()` applies the same endpoint policy without opening a connection. See [Remote Envd](../environments/remote-envd.md) for allowed URL forms, TLS, and credential ownership.

A custom `WebSocketConnection` supplies `subprotocol`, async `send`, `recv`, `close`, and `wait_closed`. The Host validates the credential before handing over the connection. Framework adapters translate disconnects to `EOFError` or `OSError`; `wait_closed` must not compete with the transport's `recv` loop.

## Device and Session ownership

`EIPDeviceConnection.initialize()` accepts the transport, `expected_device_id`, optional client name/version, `initialization_timeout=10.0`, `request_timeout=None`, and `max_in_flight=32` per Session. Use `expected_device_id=None` only during explicit first-contact registration, then retain and verify the returned identity.

A Device provides:

- `descriptor` and `describe()`: cached or freshly observed Device information, including path style, default working directory and directory-discovery availability.
- `list_directories(DirectoryListParams(...))`: bounded one-level directory listing with exact expected Device ID and generation, absolute path, offset and limit. It opens no Session.
- `open_session(working_directory=None, egress=None, required_methods=(), readiness_timeout=10.0)`: a new independent Session. Omitted cwd selects the Device default. A controlled-egress Device requires `egress`; other Devices reject it. Required methods assert compatibility, not permissions.
- `attach_session(descriptor)`: explicit attachment to the exact existing Session in the same Device generation during disconnect grace. It never replays operations or resumes transfers.
- `close()`: close locally owned Sessions and then the physical connection.

Each Session has a fixed `session_id`, generation and working directory. It owns its generated `client`, file transfers, commands, output and receipt namespace. Independent Sessions may run concurrently on any carrier. Operation IDs may be reused across different Sessions without sharing evidence.

`EIPSession` provides `describe()`, `readiness(timeout=10.0)`, `open_reader()`, `open_writer()`, `open_output()`, `observe_computer()`, `close()` and `abort()`. The client maintains Session-local keepalive while open. Session close or abort never closes a sibling Session or borrowed carrier. `abort()` makes a bounded best-effort Session close without claiming the outcome of ambiguous work.

Session descriptor refresh may narrow methods and limits; identity, generation and fixed cwd cannot change. A not-ready response or Session-local protocol failure fences that Session. Carrier corruption or loss terminates all local scopes on that connection. Keep the Device owner alive for every borrowing adapter's complete lifetime.

## Read and write binary files

EIP paths are absolute paths in the **Device filesystem namespace**, not mount-relative or Harness aggregate paths. A working directory is a default, not an access boundary. POSIX paths use `/work/report.txt`; Windows paths use `/C:/work/report.txt` or `/UNC/server/share/report.txt`:

```python
from a13n_envd_client.eip.v1 import EIPPath

path = EIPPath(path="/work/report.txt")

async with session.open_writer(path, mode="upsert") as writer:
    await writer.write(b"Hello from EIP\n")
    committed = await writer.commit()

async with session.open_reader(path) as reader:
    async for chunk in reader:
        print(chunk.decode("utf-8"), end="")
    completion = reader.completion
```

These fragments require the appropriate advertised methods and operating-system write access. Use an incremental decoder for arbitrary text streams: chunk boundaries need not coincide with UTF-8 character boundaries. For large transfers, forward bytes to a bounded application sink instead of accumulating all data in memory.

### Reader

`open_reader(path, byte_range=None, transfer_timeout_ms=None)` returns a single-entry `EIPFileReader`. It owns attachment, offset/digest validation, completion evidence, and typed reader close. `opened` exposes the negotiated open result; `open_context` exposes the generated operation context. `completion` is unavailable until terminal evidence exists.

### Writer

`open_writer(path, mode=..., executable=None, transfer_timeout_ms=None)` returns a single-entry staged `EIPFileWriter`. `mode` accepts the generated `FileWriteMode` or its string value. `write()` accepts bytes, bytearray, or memoryview, enforces transfer limits, and splits chunks to the negotiated frame size.

> [!IMPORTANT]
> **Context exit does not commit.** Call `await writer.commit()` explicitly; otherwise exit aborts the staged writer. Commit seals the stream, verifies its SHA-256/byte-count evidence, and publishes through the daemon's commit operation. `opened`, `transferred_bytes`, `open_context`, `commit_context`, and the post-commit `result` expose its evidence.

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

The reader validates contiguous offsets, exact reference, monotonic counters and completion, immutable preview prefixes, and terminal byte counts. Invalid evidence fences its owning Session for a protocol error. EOF means the producer completed and the retained end was reached; it does not mean every produced byte was retained. Inspect `OutputInfo.content_complete` and the produced/retained counters before claiming complete output.

## Timeouts, cancellation, and receipts

`RequestCoordinator` owns the single Device reader and bounded correlation/admission. `SessionRequester` scopes operation calls and binary transfers. Sent abandoned requests retain correlation and capacity until a response or terminal carrier event; a cancelled caller does not cancel a shared stdio write. Both are advanced transport-integration primitives; normal callers use an `EIPSession` and its generated client.

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
| `EIPSessionStateError`    | Invalid local Session/helper state or unavailable method             |
| `EIPTransferError`        | Transfer reset/failure; optional `status` and `offset` evidence      |

Custom carrier integrations implement `EIPTransport` and exchange `ControlFrame` or generated binary `DataFrame` values through `EIPTransportFrame`. They must preserve framing, size limits, serialization, and lifecycle; these exports are not another provisioner API.

## Generated method reference

The current generated surface below comes from `a13n_envd_client.eip.v1.METHODS`. Import parameter/result types, enums, codecs, `EIP_PROTOCOL_VERSION`, and the low-level `EIPClient` from that module. The IDL and generator own the wire schema; do not hand-edit generated files.

Availability remains the initialized descriptor's decision. A generated method existing in Python does not mean every configured daemon exposes it.

| EIP method                   | Python method                | Parameters                   | Result                       | Replay class        |
| ---------------------------- | ---------------------------- | ---------------------------- | ---------------------------- | ------------------- |
| `computer.describe`          | `computer_describe`          | `ComputerDescribeParams`     | `ComputerDescribeResult`     | `active_only`       |
| `computer.observe`           | `computer_observe`           | `ComputerObserveParams`      | `ComputerObserveResult`      | `active_only`       |
| `computer.close_observation` | `computer_close_observation` | `FileReaderCloseParams`      | `FileReaderCloseResult`      | `active_only`       |
| `computer.click`             | `computer_click`             | `ComputerClickParams`        | `ComputerActionResult`       | `terminal_evidence` |
| `computer.move`              | `computer_move`              | `ComputerMoveParams`         | `ComputerActionResult`       | `terminal_evidence` |
| `computer.drag`              | `computer_drag`              | `ComputerDragParams`         | `ComputerActionResult`       | `terminal_evidence` |
| `computer.scroll`            | `computer_scroll`            | `ComputerScrollParams`       | `ComputerActionResult`       | `terminal_evidence` |
| `computer.type_text`         | `computer_type_text`         | `ComputerTypeTextParams`     | `ComputerActionResult`       | `terminal_evidence` |
| `computer.press_keys`        | `computer_press_keys`        | `ComputerPressKeysParams`    | `ComputerActionResult`       | `terminal_evidence` |
| `device.describe`            | `device_describe`            | `DeviceDescribeParams`       | `DeviceDescribeResult`       | `ledger_external`   |
| `directory.list`             | `directory_list`             | `DirectoryListParams`        | `DirectoryListResult`        | `ledger_external`   |
| `egress.update`              | `egress_update`              | `EgressUpdateParams`         | `EgressUpdateResult`         | `ledger_external`   |
| `environment.describe`       | `environment_describe`       | `EnvironmentDescribeParams`  | `EnvironmentDescribeResult`  | `active_only`       |
| `environment.readiness`      | `environment_readiness`      | `EnvironmentReadinessParams` | `EnvironmentReadinessResult` | `active_only`       |
| `file.abort_writer`          | `file_abort_writer`          | `FileWriterAbortParams`      | `FileWriterAbortResult`      | `active_only`       |
| `file.close_reader`          | `file_close_reader`          | `FileReaderCloseParams`      | `FileReaderCloseResult`      | `active_only`       |
| `file.commit_writer`         | `file_commit_writer`         | `FileWriterCommitParams`     | `FileWriterCommitResult`     | `terminal_evidence` |
| `file.copy`                  | `file_copy`                  | `FileCopyParams`             | `FileCopyResult`             | `terminal_evidence` |
| `file.find`                  | `file_find`                  | `FileFindParams`             | `FileFindResult`             | `active_only`       |
| `file.list`                  | `file_list`                  | `FileListParams`             | `FileListResult`             | `active_only`       |
| `file.mkdir`                 | `file_mkdir`                 | `FileMkdirParams`            | `FileMkdirResult`            | `terminal_evidence` |
| `file.move`                  | `file_move`                  | `FileMoveParams`             | `FileMoveResult`             | `terminal_evidence` |
| `file.open_reader`           | `file_open_reader`           | `FileReaderOpenParams`       | `FileReaderOpenResult`       | `active_only`       |
| `file.open_writer`           | `file_open_writer`           | `FileWriterOpenParams`       | `FileWriterOpenResult`       | `active_only`       |
| `file.patch_text`            | `file_patch_text`            | `FilePatchTextParams`        | `FilePatchTextResult`        | `terminal_evidence` |
| `file.read_text`             | `file_read_text`             | `FileReadTextParams`         | `FileReadTextResult`         | `active_only`       |
| `file.remove`                | `file_remove`                | `FileRemoveParams`           | `FileRemoveResult`           | `terminal_evidence` |
| `file.search`                | `file_search`                | `FileSearchParams`           | `FileSearchResult`           | `active_only`       |
| `file.stat`                  | `file_stat`                  | `FileStatParams`             | `FileStatResult`             | `active_only`       |
| `file.write_text`            | `file_write_text`            | `FileWriteTextParams`        | `FileWriteTextResult`        | `terminal_evidence` |
| `initialize`                 | `initialize`                 | `InitializeParams`           | `InitializeResult`           | `ledger_external`   |
| `operation.cancel`           | `operation_cancel`           | `OperationCancelParams`      | `OperationCancelResult`      | `active_only`       |
| `output.read`                | `output_read`                | `OutputReadParams`           | `OutputReadResult`           | `active_only`       |
| `output.release`             | `output_release`             | `OutputReleaseParams`        | `OutputReleaseResult`        | `terminal_evidence` |
| `port.inspect`               | `port_inspect`               | `PortInspectParams`          | `PortInspectResult`          | `active_only`       |
| `port.wait`                  | `port_wait`                  | `PortWaitParams`             | `PortWaitResult`             | `active_only`       |
| `process.close_stdin`        | `process_close_stdin`        | `ProcessCloseStdinParams`    | `ProcessCloseStdinResult`    | `terminal_evidence` |
| `process.inspect`            | `process_inspect`            | `ProcessInspectParams`       | `ProcessInspectResult`       | `active_only`       |
| `process.kill`               | `process_kill`               | `ProcessKillParams`          | `ProcessKillResult`          | `terminal_evidence` |
| `process.release`            | `process_release`            | `ProcessReleaseParams`       | `ProcessReleaseResult`       | `terminal_evidence` |
| `process.signal`             | `process_signal`             | `ProcessSignalParams`        | `ProcessSignalResult`        | `terminal_evidence` |
| `process.start`              | `process_start`              | `ProcessStartParams`         | `ProcessStartResult`         | `terminal_evidence` |
| `process.wait`               | `process_wait`               | `ProcessWaitParams`          | `ProcessWaitResult`          | `active_only`       |
| `process.write_stdin`        | `process_write_stdin`        | `ProcessWriteStdinParams`    | `ProcessWriteStdinResult`    | `terminal_evidence` |
| `receipt.get`                | `receipt_get`                | `ReceiptGetParams`           | `ReceiptGetResult`           | `active_only`       |
| `session.attach`             | `session_attach`             | `SessionAttachParams`        | `SessionOpenResult`          | `ledger_external`   |
| `session.close`              | `session_close`              | `SessionCloseParams`         | `SessionCloseResult`         | `ledger_external`   |
| `session.keepalive`          | `session_keepalive`          | `SessionKeepaliveParams`     | `SessionKeepaliveResult`     | `ledger_external`   |
| `session.open`               | `session_open`               | `SessionOpenParams`          | `SessionOpenResult`          | `ledger_external`   |
| `shell.exec`                 | `shell_exec`                 | `ShellExecParams`            | `ShellExecResult`            | `terminal_evidence` |

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

The client suite covers framing, Sessions, errors, transfers, and output with protocol fixtures. Native process cleanup and daemon availability need the separate Envd integration checks. The Host, not Envd, establishes any outer sandbox. For Host-owned process launch/runtime bootstrap, use [Local Envd](index.md#try-local-envd); for application tools, use [Environment operations](../environments/operations.md).
