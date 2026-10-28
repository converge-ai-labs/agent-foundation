---
title: Python EIP client
description: Connect to a prepared Envd Device, open independent Sessions, and transfer files and process output from Python.
---

Use `a13n-envd-client` when implementing an Environment Provider or another trusted EIP integration. It handles the Device connection, Session scopes and protocol operations. For agent applications, start with [Environment Providers](../environments/index.md), which adapt those operations for Harness.

Your Host prepares the target: install and launch Envd, supply credentials, and retain target state. For reverse WebSocket, the Host also accepts and authenticates the connection. The low-level client takes over once a transport is available.

## Install

Use Python 3.13 or later:

```console
uv add a13n-envd-client
```

The client and native daemon share an Envd release version. Upgrade them together, including the daemon in custom images and remote endpoints. Recreate environments that still retain an older image. For source examples, use the repository lockfile and matching source daemon.

Protocol negotiation uses a separate wire identity. This source uses EIP `0.3`; peers offering only `0.1` or `0.2` fail negotiation. The generated Python namespace remains `eip.v1`, and the binary data-frame profile remains `1`. Repository release-dependency settings require client `>=0.5.0,<0.6.0` for Harness and `>=0.5.1,<0.6.0` for Harness UI.

## Connect to an existing HTTP daemon

Prepare a running HTTP daemon and set `ENVD_ENDPOINT` to its base URL, `ENVD_CREDENTIAL` to the Host-issued credential, and `ENVD_DEVICE_ID` to its expected identity. Use the base URL rather than `/eip/control`. Then run:

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

The example initializes the Device, describes its default directory, and opens a Session there. Initialization negotiates the protocol and checks identity without opening a Session. `open_session()` creates an independent fixed-cwd scope and checks readiness before returning.

Exiting the inner context closes that Session. Exiting the Device context closes its owned Sessions and transport. The external daemon and Device files remain available to their Host owner.

## Choose a transport

Choose the carrier your Host has prepared:

| Transport                                     | Supply                                                         | Ownership and constraints                                                                      |
| --------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `StdioTransport(reader, writer, process=...)` | Parent-owned asyncio streams                                   | Multiplexed EIP control and binary frames over trusted pipes                                   |
| `StdioTransport.from_process(process)`        | An already launched asyncio subprocess with piped stdin/stdout | Launch policy, required runtime directory, stderr draining, and containment belong to the Host |
| `HttpTransport(endpoint, credential, ...)`    | Daemon base URL and attachment credential                      | Authenticated requests and raw transfer streams; client owns its internal HTTP connection pool |
| `AcceptedWebSocketTransport(connection, ...)` | Already accepted and authenticated `WebSocketConnection`       | Requires negotiated `eip.v1`; does not listen, dial, or authenticate the upgrade               |

Request, response and transfer-frame limits default to 1 MiB each through `max_request_bytes`, `max_response_bytes` and `max_transfer_frame_bytes`. Set positive values; negotiation can narrow them to the descriptor's limits. Method availability is checked separately.

### HTTP options

| Option                              | Default              | Meaning                                                                                                  |
| ----------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------- |
| `verify`                            | `True`               | TLS verification; supports SSL context or CA path; explicit `False` disables certificate/hostname checks |
| `request_timeout`                   | `30.0` seconds       | Connection/request timeouts; control-read budgets account for operation timeouts                         |
| `allow_plaintext_private_link`      | `False`              | Explicit permission for the supported private-link HTTP case; not unrestricted plaintext                 |
| Request, response, and frame limits | `1048576` bytes each | Carrier-side bounds                                                                                      |

HTTPS connections honor `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` and lowercase equivalents through `httpx2`. Treat the deployment operator's proxy as trusted routing infrastructure. Plaintext loopback and supported provider-private links connect directly to keep credentials on the intended link. Redirects are disabled.

Configure TLS with `verify`; `SSL_CERT_FILE` and `SSL_CERT_DIR` do not select this transport's trust roots. `normalize_http_endpoint()` checks the same URL policy without connecting. [Remote Envd](../environments/remote-envd.md) explains allowed origins, TLS and credential ownership.

For a custom accepted WebSocket adapter, provide `subprotocol` and async `send`, `recv`, `close`, `wait_closed`. Authenticate before handing it to the transport. Translate framework disconnects to `EOFError` or `OSError`, and implement `wait_closed` without consuming messages needed by the transport's `recv` loop.

## Device and Session ownership

Keep one `EIPDeviceConnection` alive for every adapter borrowing its Sessions. Initialize it with the transport and `expected_device_id`. Only explicit first-contact registration should use `expected_device_id=None`; retain the returned identity and verify it on later connections.

Initialization also accepts a client name/version, `initialization_timeout=10.0`, `request_timeout=None` and per-Session `max_in_flight=32`. Device methods separate discovery from execution:

- `descriptor` reads cached Device information; `describe()` fetches a fresh view. Both expose system information, path style, default directory and directory-discovery availability.
- `metrics()` reads the latest cached sample when `device.metrics` is advertised. It opens no Session and starts no collection; `sample` remains `None` until the first collection completes. See [monitoring and sample semantics](configuration.md#system-information-and-resource-monitoring).
- `list_directories(DirectoryListParams(...))` lists one directory level with an absolute path, offset, limit, expected Device ID and generation. It opens no Session.
- `open_session(working_directory=None, egress=None, required_methods=(), readiness_timeout=10.0)` creates a fresh Session. Omitted cwd selects the Device default. Supply `egress` for controlled Devices; other modes reject it. `required_methods` checks compatibility rather than granting permissions.
- `attach_session(descriptor)` explicitly attaches to the exact existing Session in the same Device generation during disconnect grace. Operations and transfers are not replayed or resumed.
- `close()` closes locally owned Sessions, then the physical connection.

A Session owns its generated `client`, transfers, commands, output and receipt namespace. Its identity, generation and working directory stay fixed. Sessions can run concurrently on every carrier; the same operation ID in different Sessions refers to separate evidence.

Use `EIPSession.describe()` and `readiness(timeout=10.0)` to inspect the Session, and `open_reader()`, `open_writer()`, `open_output()` or `observe_computer()` for resource helpers. The client maintains keepalive while the Session is open. `close()` and `abort()` leave sibling Sessions and the borrowed carrier intact. `abort()` attempts a bounded close; uncertain operations still need reconciliation.

Descriptor refresh can narrow methods and limits. A not-ready response or Session-local protocol failure fences that Session; carrier corruption or loss terminates every local scope on the connection.

## Read and write binary files

Pass absolute paths in the **Device filesystem namespace**. POSIX uses paths such as `/work/report.txt`; Windows uses `/C:/work/report.txt` or `/UNC/server/share/report.txt`. These are neither mount-relative nor Harness aggregate paths. The working directory supplies a default, while the execution account and configured boundary determine access.

Given a ready Session with the required methods and filesystem access, write and read a file:

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

This example writes ASCII bytes. For arbitrary UTF-8 streams, use an incremental decoder because a chunk can end within a character. Forward large transfers to a bounded sink rather than collecting the entire file in memory.

### Reader

`open_reader(path, byte_range=None, transfer_timeout_ms=None)` returns an `EIPFileReader` context that can be entered once. It attaches the transfer, validates offsets and digests, and closes the reader with typed completion evidence. Inspect `opened` for the open result and `open_context` for its operation context. `completion` becomes available after terminal evidence is established.

### Writer

`open_writer(path, mode=..., executable=None, transfer_timeout_ms=None)` returns a staged `EIPFileWriter` context that can be entered once. Pass a generated `FileWriteMode` or its string value. `write()` accepts bytes, bytearray or memoryview, enforces transfer limits and splits chunks to the negotiated frame size.

> [!IMPORTANT]
> Call `await writer.commit()` to publish the file. Context exit without a successful commit aborts staged data. Commit seals the stream, verifies SHA-256 and byte counts, then invokes the daemon's commit operation.

Inspect `opened`, `transferred_bytes`, `open_context`, `commit_context` and the post-commit `result` for evidence. Retain the commit operation ID if recovery may be needed. A dispatched commit can still complete after a local timeout; abort may report it as in progress or already completed.

## Read retained process output

Use `open_output(reference, start_offset=0, observed=None)` to read retained output independently of a live process handle. The reference accepts the generated type or a string; `observed` can supply a matching earlier `OutputInfo`.

```python
reader = session.open_output(output_reference)
while not reader.eof:
    page = await reader.read_page(wait_ms=1000)
    consume_bytes(page.data)
    # Persist offsets only under the Host's own output-retention policy.
```

Supply the application's `output_reference` and `consume_bytes` in this fragment. Each `EIPOutputPage` contains `start_offset`, `next_offset`, `data`, `output` and `eof`. The reader exposes its reference, offset, latest output information and EOF state. Async iteration yields nonempty chunks and waits in one-second pages.

The reader checks reference identity, contiguous offsets, monotonic counters/completion, immutable preview prefixes and terminal byte counts. Invalid evidence fences the Session with a protocol error. EOF establishes producer completion and arrival at the retained end. To determine whether all produced bytes were retained, inspect `OutputInfo.content_complete` and the produced/retained counters.

## Timeouts, cancellation, and receipts

Give each logical operation an `EIPCallContext`. Its operation ID is 1–128 characters, separate from the JSON-RPC request ID; optional `timeout_ms` is a positive uint64. Retain the same operation identity when reconciling an operation under the method's receipt/replay rules.

An explicit `timeout_ms` sets the operation budget. Local admission has its own deadline, and response waiting allows that budget plus the coordinator's allowance: 30 seconds when no request timeout is configured. A 60-second operation with that allowance can wait 90 seconds for its response. Without an operation budget, the configured request timeout applies. Initialization and readiness retain their enclosing deadlines.

After cancellation, connection loss or a local timeout, inspect a sent mutation before deciding what to do next. Use `receipt.get`, a replay permitted by the method and retained evidence window, or native-state inspection. The client does not automatically retry ambiguous operations, and `operation.cancel` does not establish rollback.

Custom transport integrations use `RequestCoordinator` for the Device's single reader, correlation and bounded admission, and `SessionRequester` for scoped operations and transfers. Sent requests abandoned by a caller retain correlation and capacity until a response or terminal carrier event. Caller cancellation leaves a shared stdio write running. Ordinary integrations use `EIPSession` and its generated client above these primitives.

## Error reference

Use the exception and its evidence to distinguish local state, transport failure and a correlated daemon error:

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

Custom carriers implement `EIPTransport`, exchanging `ControlFrame` or generated binary `DataFrame` values through `EIPTransportFrame`. Preserve framing, size limits, serialization and lifecycle semantics. The Host continues to own provisioning.

## Generated method reference

Import parameter/result models, enums, codecs, `EIP_PROTOCOL_VERSION` and low-level `EIPClient` from `a13n_envd_client.eip.v1`. The table follows that module's `METHODS` metadata. The IDL and generator own wire definitions; change their source rather than editing generated files.

Check the initialized descriptor before calling a method. Generated Python availability and a configured daemon's advertised methods are separate checks.

| EIP method                   | Python method                | Parameters                     | Result                         | Replay class        |
| ---------------------------- | ---------------------------- | ------------------------------ | ------------------------------ | ------------------- |
| `computer.read_clipboard`    | `computer_read_clipboard`    | `ComputerReadClipboardParams`  | `ComputerReadClipboardResult`  | `active_only`       |
| `computer.write_clipboard`   | `computer_write_clipboard`   | `ComputerWriteClipboardParams` | `ComputerWriteClipboardResult` | `terminal_evidence` |
| `computer.describe`          | `computer_describe`          | `ComputerDescribeParams`       | `ComputerDescribeResult`       | `active_only`       |
| `computer.observe`           | `computer_observe`           | `ComputerObserveParams`        | `ComputerObserveResult`        | `active_only`       |
| `computer.zoom`              | `computer_zoom`              | `ComputerZoomParams`           | `ComputerZoomResult`           | `active_only`       |
| `computer.close_observation` | `computer_close_observation` | `FileReaderCloseParams`        | `FileReaderCloseResult`        | `active_only`       |
| `computer.click`             | `computer_click`             | `ComputerClickParams`          | `ComputerActionResult`         | `terminal_evidence` |
| `computer.move`              | `computer_move`              | `ComputerMoveParams`           | `ComputerActionResult`         | `terminal_evidence` |
| `computer.drag`              | `computer_drag`              | `ComputerDragParams`           | `ComputerActionResult`         | `terminal_evidence` |
| `computer.scroll`            | `computer_scroll`            | `ComputerScrollParams`         | `ComputerActionResult`         | `terminal_evidence` |
| `computer.type_text`         | `computer_type_text`         | `ComputerTypeTextParams`       | `ComputerActionResult`         | `terminal_evidence` |
| `computer.press_keys`        | `computer_press_keys`        | `ComputerPressKeysParams`      | `ComputerActionResult`         | `terminal_evidence` |
| `device.describe`            | `device_describe`            | `DeviceDescribeParams`         | `DeviceDescribeResult`         | `ledger_external`   |
| `device.metrics`             | `device_metrics`             | `DeviceMetricsParams`          | `DeviceMetricsResult`          | `ledger_external`   |
| `directory.list`             | `directory_list`             | `DirectoryListParams`          | `DirectoryListResult`          | `ledger_external`   |
| `egress.update`              | `egress_update`              | `EgressUpdateParams`           | `EgressUpdateResult`           | `ledger_external`   |
| `environment.describe`       | `environment_describe`       | `EnvironmentDescribeParams`    | `EnvironmentDescribeResult`    | `active_only`       |
| `environment.readiness`      | `environment_readiness`      | `EnvironmentReadinessParams`   | `EnvironmentReadinessResult`   | `active_only`       |
| `file.abort_writer`          | `file_abort_writer`          | `FileWriterAbortParams`        | `FileWriterAbortResult`        | `active_only`       |
| `file.close_reader`          | `file_close_reader`          | `FileReaderCloseParams`        | `FileReaderCloseResult`        | `active_only`       |
| `file.commit_writer`         | `file_commit_writer`         | `FileWriterCommitParams`       | `FileWriterCommitResult`       | `terminal_evidence` |
| `file.copy`                  | `file_copy`                  | `FileCopyParams`               | `FileCopyResult`               | `terminal_evidence` |
| `file.find`                  | `file_find`                  | `FileFindParams`               | `FileFindResult`               | `active_only`       |
| `file.list`                  | `file_list`                  | `FileListParams`               | `FileListResult`               | `active_only`       |
| `file.mkdir`                 | `file_mkdir`                 | `FileMkdirParams`              | `FileMkdirResult`              | `terminal_evidence` |
| `file.move`                  | `file_move`                  | `FileMoveParams`               | `FileMoveResult`               | `terminal_evidence` |
| `file.open_reader`           | `file_open_reader`           | `FileReaderOpenParams`         | `FileReaderOpenResult`         | `active_only`       |
| `file.open_writer`           | `file_open_writer`           | `FileWriterOpenParams`         | `FileWriterOpenResult`         | `active_only`       |
| `file.patch_text`            | `file_patch_text`            | `FilePatchTextParams`          | `FilePatchTextResult`          | `terminal_evidence` |
| `file.read_text`             | `file_read_text`             | `FileReadTextParams`           | `FileReadTextResult`           | `active_only`       |
| `file.remove`                | `file_remove`                | `FileRemoveParams`             | `FileRemoveResult`             | `terminal_evidence` |
| `file.search`                | `file_search`                | `FileSearchParams`             | `FileSearchResult`             | `active_only`       |
| `file.stat`                  | `file_stat`                  | `FileStatParams`               | `FileStatResult`               | `active_only`       |
| `file.write_text`            | `file_write_text`            | `FileWriteTextParams`          | `FileWriteTextResult`          | `terminal_evidence` |
| `initialize`                 | `initialize`                 | `InitializeParams`             | `InitializeResult`             | `ledger_external`   |
| `operation.cancel`           | `operation_cancel`           | `OperationCancelParams`        | `OperationCancelResult`        | `active_only`       |
| `output.read`                | `output_read`                | `OutputReadParams`             | `OutputReadResult`             | `active_only`       |
| `output.release`             | `output_release`             | `OutputReleaseParams`          | `OutputReleaseResult`          | `terminal_evidence` |
| `port.inspect`               | `port_inspect`               | `PortInspectParams`            | `PortInspectResult`            | `active_only`       |
| `port.wait`                  | `port_wait`                  | `PortWaitParams`               | `PortWaitResult`               | `active_only`       |
| `process.close_stdin`        | `process_close_stdin`        | `ProcessCloseStdinParams`      | `ProcessCloseStdinResult`      | `terminal_evidence` |
| `process.inspect`            | `process_inspect`            | `ProcessInspectParams`         | `ProcessInspectResult`         | `active_only`       |
| `process.kill`               | `process_kill`               | `ProcessKillParams`            | `ProcessKillResult`            | `terminal_evidence` |
| `process.release`            | `process_release`            | `ProcessReleaseParams`         | `ProcessReleaseResult`         | `terminal_evidence` |
| `process.signal`             | `process_signal`             | `ProcessSignalParams`          | `ProcessSignalResult`          | `terminal_evidence` |
| `process.start`              | `process_start`              | `ProcessStartParams`           | `ProcessStartResult`           | `terminal_evidence` |
| `process.wait`               | `process_wait`               | `ProcessWaitParams`            | `ProcessWaitResult`            | `active_only`       |
| `process.write_stdin`        | `process_write_stdin`        | `ProcessWriteStdinParams`      | `ProcessWriteStdinResult`      | `terminal_evidence` |
| `receipt.get`                | `receipt_get`                | `ReceiptGetParams`             | `ReceiptGetResult`             | `active_only`       |
| `session.attach`             | `session_attach`             | `SessionAttachParams`          | `SessionOpenResult`            | `ledger_external`   |
| `session.close`              | `session_close`              | `SessionCloseParams`           | `SessionCloseResult`           | `ledger_external`   |
| `session.keepalive`          | `session_keepalive`          | `SessionKeepaliveParams`       | `SessionKeepaliveResult`       | `ledger_external`   |
| `session.open`               | `session_open`               | `SessionOpenParams`            | `SessionOpenResult`            | `ledger_external`   |
| `shell.exec`                 | `shell_exec`                 | `ShellExecParams`              | `ShellExecResult`              | `terminal_evidence` |

Inspect a request model's schema for exact fields and validation constraints:

```python
from a13n_envd_client.eip.v1 import FileReadTextParams, METHODS

schema = FileReadTextParams.model_json_schema()
assert "context" in schema["properties"]
assert "file.read_text" in METHODS
```

The [EIP contract](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd) explains outcomes, receipt windows, transfer integrity and compatibility. Use its replay rules when recovering an uncertain mutation; a new operation ID represents new work.

## Validate and choose a higher-level API

Run the protocol-fixture client suite from a repository checkout:

```console
uv run --locked pytest packages/a13n-envd-client/tests
```

These tests cover framing, Sessions, errors, transfers and output. Use Envd integration checks for native cleanup and daemon availability. The Host establishes the outer sandbox. Choose [Local Envd](index.md#try-local-envd) for Host-owned process launch and runtime setup, or [Environment operations](../environments/operations.md) for application tools.
