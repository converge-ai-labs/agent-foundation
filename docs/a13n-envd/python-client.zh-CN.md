---
title: Python EIP 客户端
description: 连接已准备好的 Envd 设备，打开独立会话，并通过 Python 传输文件和读取进程输出。
---

实现环境 Provider 或其他可信 EIP 集成时，使用 `a13n-envd-client`。它处理设备连接、会话范围和协议操作。agent 应用可从[环境 Provider](../environments/index.md)开始，由其将操作适配给 Harness。

Host 负责准备目标：安装并启动 Envd、提供凭据、保留目标状态。使用反向 WebSocket 时，Host 还负责接受连接并验证身份。传输通道就绪后，再交给底层客户端。

## 安装

使用 Python 3.13 或更新版本：

```console
uv add a13n-envd-client
```

客户端和原生守护进程使用相同的 Envd 发布版本。升级时一起更新，包括自定义镜像和远程端点中的守护进程。仍保留旧镜像的环境应重新创建。运行源码示例时，使用仓库锁文件和匹配的源码守护进程。

协议协商使用独立的线上协议标识。当前源码使用 EIP `0.3`；只提供 `0.1` 或 `0.2` 的对端会协商失败。生成的 Python 命名空间仍为 `eip.v1`，二进制数据帧 profile 仍为 `1`。仓库发布依赖设置中，Harness 要求 client `>=0.5.0,<0.6.0`，Harness UI 要求 `>=0.5.1,<0.6.0`。

## 连接已有 HTTP 守护进程

准备运行中的 HTTP 守护进程，将 `ENVD_ENDPOINT` 设为其基础 URL，`ENVD_CREDENTIAL` 设为 Host 签发的凭据，`ENVD_DEVICE_ID` 设为预期身份。使用基础 URL，而非 `/eip/control`。然后运行：

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

示例初始化设备、查询默认目录，并在该目录打开会话。初始化协商协议、检查身份，但不打开会话。`open_session()` 创建独立的固定 cwd 范围，检查就绪后返回。

退出内层上下文会关闭该会话。退出设备上下文会关闭其管理的会话和传输通道。外部守护进程和设备文件仍由 Host 管理。

## 选择传输方式

选择 Host 已准备好的传输通道：

| 传输                                          | 提供内容                                            | 归属与约束                                               |
| --------------------------------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| `StdioTransport(reader, writer, process=...)` | 父进程管理的 asyncio 流                             | 通过可信管道复用 EIP 控制和二进制帧                      |
| `StdioTransport.from_process(process)`        | 已启动且通过管道连接 stdin/stdout 的 asyncio 子进程 | 启动策略、必需运行时目录、stderr 消费和隔离由 Host 负责  |
| `HttpTransport(endpoint, credential, ...)`    | 守护进程基础 URL 和附加凭据                         | 经身份验证的请求和原始传输流；客户端管理内部 HTTP 连接池 |
| `AcceptedWebSocketTransport(connection, ...)` | 已接受并完成身份验证的 `WebSocketConnection`        | 要求已协商 `eip.v1`；不监听、拨号或验证升级              |

请求、响应和传输帧分别通过 `max_request_bytes`、`max_response_bytes` 和 `max_transfer_frame_bytes` 限制，默认各为 1 MiB。设置值必须为正数；协商可以将其收窄到描述符限制。方法可用性另行检查。

### HTTP 选项

| 选项                           | 默认值            | 含义                                                                    |
| ------------------------------ | ----------------- | ----------------------------------------------------------------------- |
| `verify`                       | `True`            | TLS 验证；支持 SSL context 或 CA 路径；显式 `False` 关闭证书/主机名检查 |
| `request_timeout`              | `30.0` 秒         | 连接/请求超时；控制读取预算会计入操作超时                               |
| `allow_plaintext_private_link` | `False`           | 显式允许支持的私有链路 HTTP 场景，并非不受限制的明文访问                |
| 请求、响应和帧限制             | 各 `1048576` 字节 | 传输侧边界                                                              |

HTTPS 连接通过 `httpx2` 遵循 `HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写形式。部署运维人员的代理属于可信路由基础设施。明文回环和受支持的 provider 私有链路保持直连，让凭据留在预期链路上。重定向被禁用。

通过 `verify` 配置 TLS；`SSL_CERT_FILE` 和 `SSL_CERT_DIR` 不决定该传输的信任根。`normalize_http_endpoint()` 检查相同的 URL 策略，但不建立连接。允许的 origin、TLS 和凭据归属见 [Remote Envd](../environments/remote-envd.md)。

自定义已接受 WebSocket 的适配器需提供 `subprotocol`，以及异步 `send`、`recv`、`close`、`wait_closed`。交给传输组件前先完成身份验证。将框架断开事件转换为 `EOFError` 或 `OSError`；实现 `wait_closed` 时，不要消费传输 `recv` 循环所需的消息。

## 设备与会话归属

只要适配器仍借用会话，就保持所属 `EIPDeviceConnection` 存活。使用传输通道和 `expected_device_id` 初始化。仅在显式首次接触注册时使用 `expected_device_id=None`；保存返回的身份，并在后续连接时验证。

初始化还接受客户端名称/版本、`initialization_timeout=10.0`、`request_timeout=None`，以及逐会话的 `max_in_flight=32`。设备方法将发现与执行分开：

- `descriptor` 读取缓存设备信息；`describe()` 获取新视图。两者提供系统信息、路径格式、默认目录和目录发现可用性。
- 设备声明 `device.metrics` 时，`metrics()` 读取最新缓存样本。它不打开会话，也不启动采集；首次采集完成前，`sample` 保持 `None`。参见[监控与样本语义](configuration.md#system-information-and-resource-monitoring)。
- `list_directories(DirectoryListParams(...))` 使用绝对路径、偏移量、上限、预期设备 ID 和代际列出一层目录。它不打开会话。
- `open_session(working_directory=None, egress=None, required_methods=(), readiness_timeout=10.0)` 创建新会话。省略 cwd 时采用设备默认值。controlled 设备需提供 `egress`；其他模式拒绝该字段。`required_methods` 检查兼容性，不授予权限。
- `attach_session(descriptor)` 在断线宽限期内显式附加到同一设备代际的精确已有会话。操作和传输不会被重放或恢复。
- `close()` 先关闭本地管理的会话，再关闭物理连接。

会话管理自己的生成代码客户端 `client`、传输、命令、输出和回执命名空间。身份、代际和工作目录保持固定。会话可在所有传输通道上并发运行；不同会话中的相同操作 ID 对应不同证据。

通过 `EIPSession.describe()` 和 `readiness(timeout=10.0)` 检查会话，通过 `open_reader()`、`open_writer()`、`open_output()` 或 `observe_computer()` 使用资源 helper。客户端在会话打开期间维护保活。`close()` 和 `abort()` 保留同级会话及借用的传输通道。`abort()` 尝试在有限时间内关闭；结果不确定的操作仍需核对。

刷新描述符可以收窄方法和限制。未就绪响应或会话内协议失败会阻止继续使用该会话；传输损坏或丢失会终止连接上的全部本地范围。

## 读写二进制文件

传入**设备文件系统命名空间**中的绝对路径。POSIX 使用 `/work/report.txt` 等路径；Windows 使用 `/C:/work/report.txt` 或 `/UNC/server/share/report.txt`。这些既不是挂载相对路径，也不是 Harness 聚合路径。工作目录提供默认位置，执行账号和配置的边界决定访问权限。

已有就绪会话、所需方法及文件系统访问权限时，可写入并读取文件：

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

此例写入 ASCII 字节。处理任意 UTF-8 流时，使用增量解码器，因为分块可能在字符中间结束。大型传输应转发到有界接收端，不要将整个文件累积在内存中。

### 文件读取器（Reader）

`open_reader(path, byte_range=None, transfer_timeout_ms=None)` 返回只能进入一次的 `EIPFileReader` 上下文。它附加传输、验证偏移量和摘要，并以类型化完成证据关闭 reader。通过 `opened` 查看打开结果，通过 `open_context` 查看操作上下文。建立终结证据后，`completion` 才可用。

### 文件写入器（Writer）

`open_writer(path, mode=..., executable=None, transfer_timeout_ms=None)` 返回只能进入一次的暂存 `EIPFileWriter` 上下文。传入生成的 `FileWriteMode` 或其字符串值。`write()` 接受 bytes、bytearray 或 memoryview，实施传输限制，并按协商帧大小拆分块。

> [!IMPORTANT]
> 调用 `await writer.commit()` 才会发布文件。未成功提交便退出上下文时，会中止暂存数据。提交会封闭流、验证 SHA-256 和字节计数，再调用守护进程的提交操作。

通过 `opened`、`transferred_bytes`、`open_context`、`commit_context` 和提交后的 `result` 查看证据。可能需要恢复时，保留提交操作 ID。已分派的提交可能在本地超时后完成；中止操作也可能报告提交正在进行或已经完成。

## 读取保留的进程输出

使用 `open_output(reference, start_offset=0, observed=None)` 读取保留输出，无需依赖活跃进程句柄。引用接受生成类型或字符串；`observed` 可提供匹配的既有 `OutputInfo`。

```python
reader = session.open_output(output_reference)
while not reader.eof:
    page = await reader.read_page(wait_ms=1000)
    consume_bytes(page.data)
    # Persist offsets only under the Host's own output-retention policy.
```

为此片段提供应用自己的 `output_reference` 和 `consume_bytes`。每个 `EIPOutputPage` 包含 `start_offset`、`next_offset`、`data`、`output` 和 `eof`。reader 提供引用、偏移量、最新输出信息和 EOF 状态。异步迭代产出非空块，以一秒一页的方式等待。

reader 检查引用身份、连续偏移量、单调计数器/完成状态、不可变预览前缀和终结字节计数。无效证据会以协议错误阻止继续使用会话。EOF 表示生产方已完成且已到达保留内容末尾。是否保留了全部产出字节，应检查 `OutputInfo.content_complete` 和产生/保留计数器。

## 超时、取消与回执

为每个逻辑操作提供 `EIPCallContext`。操作 ID 长度为 1–128 字符，与 JSON-RPC 请求 ID 分开；可选 `timeout_ms` 为正 uint64。按方法的回执/重放规则核对操作时，保留相同操作身份。

显式 `timeout_ms` 设置操作预算。本地接纳有独立期限，响应等待允许该预算再加协调器余量：未配置请求超时时，余量为 30 秒。60 秒操作加上该余量，可以等待响应 90 秒。未提供操作预算时，采用配置的请求超时。初始化和就绪保留各自外层期限。

取消、连接丢失或本地超时后，先检查已发送的修改，再决定下一步。使用 `receipt.get`、方法和保留证据窗口允许的重放，或原生状态检查。客户端不会自动重试结果不确定的操作，`operation.cancel` 也不证明回滚。

自定义传输集成使用 `RequestCoordinator` 管理设备的单个 reader、请求关联和有界接纳，使用 `SessionRequester` 管理会话范围内的操作与传输。已发送但被调用者放弃的请求，在响应或传输终结事件到来前保留关联状态和容量。调用者取消后，共享 stdio 写入继续运行。普通集成使用这些组件之上的 `EIPSession` 和生成客户端。

## 错误参考

结合异常及其证据，区分本地状态、传输失败和已关联的守护进程错误：

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

自定义传输实现 `EIPTransport`，通过 `EIPTransportFrame` 交换 `ControlFrame` 或生成的二进制 `DataFrame`。保留消息帧、大小限制、序列化和生命周期语义。资源供应仍由 Host 负责。

## 生成的方法参考

从 `a13n_envd_client.eip.v1` 导入参数/结果模型、枚举、编解码器、`EIP_PROTOCOL_VERSION` 和底层 `EIPClient`。下表遵循该模块的 `METHODS` 元数据。线上定义由 IDL 和生成器管理；应修改来源，不要直接编辑生成文件。

调用方法前，检查初始化后的描述符。Python 中存在生成方法，与配置的守护进程声明支持该方法，是两项独立检查。

| EIP 方法                     | Python 方法                  | 参数                           | 结果                           | 重放类别            |
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

通过请求模型的 schema 查看精确字段和验证约束：

```python
from a13n_envd_client.eip.v1 import FileReadTextParams, METHODS

schema = FileReadTextParams.model_json_schema()
assert "context" in schema["properties"]
assert "file.read_text" in METHODS
```

[EIP 契约](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd)解释操作结果、回执窗口、传输完整性和兼容性。恢复结果不确定的修改时，遵循其中的重放规则；新操作 ID 代表新的工作。

## 验证与选择高层 API

在仓库 checkout 中运行协议 fixture 客户端套件：

```console
uv run --locked pytest packages/a13n-envd-client/tests
```

这些测试覆盖消息帧、会话、错误、传输和输出。原生清理与守护进程可用性由 Envd 集成检查验证。外层沙箱由 Host 建立。Host 管理进程启动和运行时准备时，选择 [Local Envd](index.md#try-local-envd)；应用工具选择[环境操作](../environments/operations.md)。
