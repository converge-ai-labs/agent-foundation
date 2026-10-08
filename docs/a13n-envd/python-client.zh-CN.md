---
title: Python EIP 客户端
description: 用于实现环境 provider 和其他可信 EIP 客户端的底层 Python 客户端。
---

客户端以 `a13n-envd-client` 发布。多数 agent 应用应使用[环境 provider](../environments/index.md)，它们已经处理目标生命周期和适配。

此包不会发现、安装、下载或启动 Envd，也不会创建反向 WebSocket 监听器、签发凭据、保留目标状态或运行 agent。

## 安装

```console
uv add a13n-envd-client
```

需要 Python 3.13 或更新版本。Python 客户端与原生守护进程使用相同的 Envd 发布版本；EIP 协商的线上协议版本是独立标识。源码示例应使用仓库锁文件，不要将发布版客户端与源码版本守护进程混用。

## 连接已有 HTTP 守护进程

这个完整示例需要运行中的守护进程、预期设备身份和 Host 签发的凭据。它不会配置基础设施，也不会离线测试账号访问。

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

端点是守护进程基础 URL，不是 `/eip/control`。设备初始化协商协议并验证身份，不打开会话。`open_session()` 创建独立的固定 cwd 范围并检查就绪状态。退出会话上下文仅关闭该会话。退出设备上下文会关闭其会话和物理传输，不关闭外部守护进程，也不影响设备上的文件。

## 选择传输方式

| 传输                                          | 提供内容                                            | 归属与约束                                               |
| --------------------------------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| `StdioTransport(reader, writer, process=...)` | 父进程管理的 asyncio 流                             | 通过可信管道复用 EIP 控制和二进制帧                      |
| `StdioTransport.from_process(process)`        | 已启动且通过管道连接 stdin/stdout 的 asyncio 子进程 | 启动策略、必需运行时目录、stderr 消费和隔离由 Host 负责  |
| `HttpTransport(endpoint, credential, ...)`    | 守护进程基础 URL 和附加凭据                         | 经身份验证的请求和原始传输流；客户端管理内部 HTTP 连接池 |
| `AcceptedWebSocketTransport(connection, ...)` | 已接受并完成身份验证的 `WebSocketConnection`        | 要求已协商 `eip.v1`；不监听、拨号或验证升级              |

所有传输的 `max_request_bytes`、`max_response_bytes` 和 `max_transfer_frame_bytes` 均默认为 1 MiB；限制必须为正数，并收窄到协商后的描述符范围。提高客户端限制不能授予服务器不支持的方法。

### HTTP 选项

| 选项                           | 默认值            | 含义                                                                    |
| ------------------------------ | ----------------- | ----------------------------------------------------------------------- |
| `verify`                       | `True`            | TLS 验证；支持 SSL context 或 CA 路径；显式 `False` 关闭证书/主机名检查 |
| `request_timeout`              | `30.0` 秒         | 连接/请求超时；控制读取预算会计入操作超时                               |
| `allow_plaintext_private_link` | `False`           | 显式允许支持的私有链路 HTTP 场景，并非不受限制的明文访问                |
| 请求、响应和帧限制             | 各 `1048576` 字节 | 传输侧边界                                                              |

传输不跟随重定向。HTTPS 附加连接通过 `httpx2` 遵循 `HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写形式；信任部署运维人员的代理来路由连接。明文回环和 provider 私有链路连接保持直连，确保凭据不会离开预期链路。TLS 验证仍使用 `verify`，不使用 `SSL_CERT_FILE` 或 `SSL_CERT_DIR`。`normalize_http_endpoint()` 应用相同端点策略，但不打开连接。允许的 URL 格式、TLS 和凭据归属见 [Remote Envd](../environments/remote-envd.md)。

自定义 `WebSocketConnection` 提供 `subprotocol`、异步 `send`、`recv`、`close` 和 `wait_closed`。Host 在交出连接前验证凭据。框架适配器将断开转换为 `EOFError` 或 `OSError`；`wait_closed` 不能与传输的 `recv` 循环竞争。

## 设备与会话归属

`EIPDeviceConnection.initialize()` 接受传输、`expected_device_id`、可选客户端名称/版本、`initialization_timeout=10.0`、`request_timeout=None`，以及逐会话的 `max_in_flight=32`。仅在显式首次接触注册时使用 `expected_device_id=None`，随后保存并验证返回的身份。

设备提供：

- `descriptor` 和 `describe()`：缓存或最新观测的设备信息，包括路径格式、默认工作目录和目录发现可用性。
- `list_directories(DirectoryListParams(...))`：有界的单层目录列表，携带精确预期设备 ID 与代次、绝对路径、偏移量和上限。不打开会话。
- `open_session(working_directory=None, egress=None, required_methods=(), readiness_timeout=10.0)`：新的独立会话。省略 cwd 时选择设备默认值。受控出站网络设备要求提供 `egress`；其他设备会拒绝它。必需方法用于断言兼容性，不是权限。
- `attach_session(descriptor)`：在断线宽限期内，显式附加到同一设备代次中的精确已有会话。绝不重放操作或恢复传输。
- `close()`：关闭本地管理的会话，然后关闭物理连接。

每个会话具有固定 `session_id`、代次和工作目录，管理自己的生成式 `client`、文件传输、命令、输出和回执命名空间。独立会话可在任意传输通道上并发运行。不同会话可以复用操作 ID，但不共享证据。

`EIPSession` 提供 `describe()`、`readiness(timeout=10.0)`、`open_reader()`、`open_writer()`、`open_output()`、`observe_computer()`、`close()` 和 `abort()`。客户端在会话打开期间维护会话内保活。关闭或中止会话绝不会关闭同级会话或借用的传输通道。`abort()` 会在有限时间内尽力关闭会话，但不声称结果不确定的工作已经结束。

刷新会话描述符可以收窄方法和限制；身份、代次和固定 cwd 不能改变。未就绪响应或会话内协议失败会隔离该会话。传输损坏或丢失会终止该连接上的全部本地范围。在每个借用适配器的整个生命周期中，保持设备所有者存活。

## 读写二进制文件

EIP 路径是**设备文件系统命名空间** 中的绝对路径，不是挂载相对路径或 Harness 聚合路径。工作目录是默认值，不是访问边界。POSIX 使用 `/work/report.txt`；Windows 使用 `/C:/work/report.txt` 或 `/UNC/server/share/report.txt`：

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

这些片段要求声明了对应方法，并具有操作系统写入权限。任意文本流应使用增量解码器：分块边界不一定与 UTF-8 字符边界一致。大型传输应将字节转发到有界的应用接收端，不要在内存中累积全部数据。

### 文件读取器（Reader）

`open_reader(path, byte_range=None, transfer_timeout_ms=None)` 返回只能进入一次的 `EIPFileReader`。它管理附加、偏移量/摘要验证、完成证据和类型化 reader 关闭。`opened` 提供协商后的打开结果；`open_context` 提供生成的操作上下文。在出现终结证据前，`completion` 不可用。

### 文件写入器（Writer）

`open_writer(path, mode=..., executable=None, transfer_timeout_ms=None)` 返回只能进入一次、采用暂存写入的 `EIPFileWriter`。`mode` 接受生成的 `FileWriteMode` 或字符串值。`write()` 接受 bytes、bytearray 或 memoryview，实施传输限制，并按协商后的帧大小拆分块。

> [!IMPORTANT]
> **退出上下文不会提交。** 必须显式调用 `await writer.commit()`；否则退出会中止暂存 writer。提交封闭流，验证 SHA-256 和字节计数证据，并通过守护进程提交操作发布文件。`opened`、`transferred_bytes`、`open_context`、`commit_context` 和提交后的 `result` 提供相应证据。

可能需要恢复时，保留提交操作 ID。分派后超时不能证明已经回滚；即使中止也可能报告提交正在进行或已经完成。

## 读取保留的进程输出

`open_output(reference, start_offset=0, observed=None)` 返回 `EIPOutputReader`，不是活跃进程句柄。`reference` 接受生成的类型或其字符串值；`observed` 可携带匹配的先前 `OutputInfo`。

```python
reader = session.open_output(output_reference)
while not reader.eof:
    page = await reader.read_page(wait_ms=1000)
    consume_bytes(page.data)
    # Persist offsets only under the Host's own output-retention policy.
```

片段中的 `output_reference` 和 `consume_bytes` 由应用管理。`EIPOutputPage` 包含 `start_offset`、`next_offset`、`data`、`output` 和 `eof`。reader 属性提供 `reference`、`offset`、最新 `output` 和 `eof`。异步迭代产出非空字节块，以一秒一页的方式等待直到 EOF。

reader 验证连续偏移量、精确引用、单调计数器与完成状态、不可变预览前缀和终结字节计数。证据无效会因协议错误隔离所属会话。EOF 表示生产方完成且已读到保留内容末尾，不代表所有产生的字节都被保留。声称输出完整前，检查 `OutputInfo.content_complete` 和产生/保留计数器。

## 超时、取消与回执

`RequestCoordinator` 管理单个设备 reader，以及有界的请求关联和接纳。`SessionRequester` 为操作调用和二进制传输限定范围。已发送但被调用者放弃的请求，在收到响应或传输终结事件前仍占用关联状态和容量；调用者取消不会取消共享 stdio 写入。两者都是高级传输集成基础组件；普通调用者使用 `EIPSession` 及其生成客户端。

`EIPCallContext` 要求 1–128 字符的操作 ID，可选提供正 uint64 `timeout_ms`。操作 ID 与 JSON-RPC 请求 ID 不同。方法的回执/重放语义要求结果核对时，提供稳定的操作 ID。

提供 `timeout_ms` 时，本地接纳单独限时；响应等待允许操作预算加协调器余量（未配置请求超时时为 30 秒）。未提供操作预算时，应用配置的请求超时。因此，60 秒操作加 30 秒余量允许等待响应 90 秒。初始化和就绪仍受外层期限限制。

取消、传输断开或本地超时不能证明已发送修改失败或不存在。客户端不会自动重试结果不确定的操作。发出不同操作前，通过 `receipt.get`、方法和证据窗口允许的重放，或原生状态检查核对结果。`operation.cancel` 本身不会把不确定性变为回滚。

## 错误参考

| 异常                      | 含义 / 证据                                   |
| ------------------------- | --------------------------------------------- |
| `EIPClientError`          | 客户端失败基类                                |
| `EIPProtocolError`        | 无效的消息帧、关联或协议证据                  |
| `EIPTransportError`       | 收到有效关联响应前传输失败                    |
| `EIPTransportClosedError` | 传输通道已关闭，可能仍有在途请求              |
| `EIPConnectionError`      | 连接建立/交换失败，不能证明未分派             |
| `EIPRequestTimeoutError`  | 本地等待过期；检查 `dispatched`               |
| `EIPMethodError`          | 有效关联的 EIP 错误；检查类型化 `error`       |
| `EIPSessionStateError`    | 本地会话/辅助组件状态无效，或方法不可用       |
| `EIPTransferError`        | 传输重置/失败；可选 `status` 和 `offset` 证据 |

自定义传输集成实现 `EIPTransport`，通过 `EIPTransportFrame` 交换 `ControlFrame` 或生成的二进制 `DataFrame` 值。必须保留消息帧、大小限制、序列化和生命周期语义；这些导出项不是另一套资源供应 API。

## 生成的方法参考

下面的当前生成接口来自 `a13n_envd_client.eip.v1.METHODS`。从该模块导入参数/结果类型、枚举、编解码器、`EIP_PROTOCOL_VERSION` 和底层 `EIPClient`。IDL 和生成器管理线上 schema；不要手动编辑生成文件。

可用性仍由初始化后的描述符决定。Python 中存在生成的方法，不代表每个配置的守护进程都提供该方法。

| EIP 方法                     | Python 方法                  | 参数                         | 结果                         | 重放类别            |
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

构建请求时检查精确的版本字段和验证约束：

```python
from a13n_envd_client.eip.v1 import FileReadTextParams, METHODS

schema = FileReadTextParams.model_json_schema()
assert "context" in schema["properties"]
assert "file.read_text" in METHODS
```

[EIP 契约](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd)定义操作结果、回执窗口、传输完整性和兼容性。生成的方法元数据记录重放类别，不代表允许用新操作 ID 重复结果不确定的修改。

## 验证与选择高层 API

```console
uv run --locked pytest packages/a13n-envd-client/tests
```

客户端套件通过协议 fixture 覆盖消息帧、会话、错误、传输和输出。原生进程清理和守护进程可用性需要独立的 Envd 集成检查。外层沙箱由 Host 建立，而非 Envd。Host 管理进程启动和运行时初始化时，使用 [Local Envd](index.md#try-local-envd)；应用工具使用[环境操作](../environments/operations.md)。
