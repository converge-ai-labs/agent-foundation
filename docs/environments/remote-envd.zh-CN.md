---
title: 连接远程 Envd
sidebarTitle: 远程 Envd
description: 将已有 Device 连接到 Host，打开独立 Session，并在恢复时避免重放操作。
---

按连接方向选择传输：`http_envd` 让 Host 主动连接已有 HTTP(S) 守护进程；`websocket_envd` 让守护进程回连 Host，也适用于 NAT 后的机器。两者都提供基于 EIP 的文件、shell、进程、输出和端口操作。

运维人员部署并维护远程机器和守护进程。Host 验证 Device 身份，并决定何时、从何处使用它。这些 provider 只负责连接：打开或关闭执行对象时，目标的创建、续期、停止和删除仍由运维人员负责。

## 先在本地试用两种传输

准备 Rust 工具链、Python 3.13 或更高版本以及 `uv`。从仓库根目录构建 Envd、安装独立示例项目，再分别运行两种传输演示：

```bash
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked

uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd

uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

检查输出是否包含：

```text
re-entry read: hello from remote envd
independent Sessions: True
provider close preserved remote daemon and workspace
```

按顺序观察演示：启动临时守护进程，通过一个 Session 写入，关闭该执行对象，再通过同一 connector 上的新 Session 读取。程序检查守护进程和文件是否在执行关闭后保留。最后，运维代码停止守护进程并移除临时工作目录。

这个本地练习使用回环连接、临时凭据和文件操作，禁用命令执行，无需模型、Docker 或云账号。在自己的应用中复制 [Host 集成](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote.py)；另读[演示辅助代码](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote_demo.py)来了解本地启动和清理。

## 连接已有 HTTP 守护进程

打开连接前准备四项输入：

1. 获取运维人员提供的 HTTP(S) origin，例如 `https://envd.example.com`。不要附加 `/eip/control`，客户端会构建资源路径。
2. 通过受保护渠道获取配置的 `A13N_ENVD_DEVICE_ID` 和凭据。
3. 选择已有的设备绝对工作目录，例如 `/work/project`。
4. 构建 Harness executable 和 Host 自有的 [`PreparedSource` 辅助类](../a13n-harness/environments.md#supply-a-source)。

以下集成片段假设 `executable`、`PreparedSource` 和 `token_from_your_secret_store` 已存在。请替换示例端点、Device ID 和目录：

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.models import EnvironmentState

(http_envd,) = select_builtin_environment_providers(("http_envd",))
state = EnvironmentState(
    provider_key=http_envd.type,
    state_version="1",
    state={"device_id": "env-remote-machine"},
)
connector = http_envd.execution_connector(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",  # Your Host's logical identity.
    state=state,
)

# PreparedSource comes from the Host integration above.
# Harness opens and closes a fresh execution for this Run.
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

`environment_id` 为环境指定 Host 内的名称；状态载荷中的 `device_id` 选择预期的远程 Device。在 Harness 之外，可用 `async with await connector.open() as execution` 打开同一 connector，再调用操作接口。如果提供共享的 `HttpEnvdProviderRuntime`，请在使用者存活期间保持其打开，并在 Host 关闭时清理。

要通过示例 CLI 完成文件读写，请在安装项目后，从 `examples/environment-provider` 运行：

```bash
uv run environment-provider-example http_envd \
  --endpoint https://envd.example.com \
  --device-id env-remote-machine \
  --credential-file /private/envd-token
```

CLI 在 Device 的默认 cwd 下写入或替换 `provider-example.txt`，再通过新 Session 读取。守护进程必须允许 `file.write_text` 和 `file.read_text`。将 token 放在受保护文件中，不要放入 URL 或命令行。

公共端点使用 HTTPS。回环地址支持明文 HTTP；可信 provider 私有链路需设置 `allow_plaintext_private_link=True`。HTTPS 使用环境代理，明文直接连接。私有 CA 通过 `HttpEnvdProviderRuntime.verify` 提供 SSL context 或 CA 文件，在构建运行时前选定。默认启用验证；`verify=False` 显式关闭证书和主机名检查。进程默认值见 [Host 出站连接](configuration.md#host-outbound-connections)。

## 会话出站网络与凭据引用

让 Session 配置匹配 Device 的启动策略。`controlled` Device 要求每个 Session 显式提供目标；`inherit` 和 `deny` Device 拒绝 Session 策略。本地、HTTP 和 WebSocket Envd 使用相同的只含引用的配置。

对于以禁用 Sandbox、受控出站网络模式启动的 Device，以下配置允许 GitHub API 流量，并指定 Host 侧凭据来源：

```python
recipe = {
    "working_directory": "/work/project",
    "egress": {
        "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
        "secrets": [{
            "env": "GH_TOKEN",
            "source": {"kind": "environment", "name": "HOST_GITHUB_TOKEN"},
            "inject_hosts": ["api.github.com"],
        }],
    },
    "expected_boundary": {
        "sandbox": {"mode": "disabled"},
        "egress": "controlled",
    },
}
```

在 Host 环境中设置 `HOST_GITHUB_TOKEN`，再将 `recipe` 作为第一个参数传给 `execution_connector`。运行时在打开每个 Session 前解析来源，值缺失或为空会导致准备失败。保存的配置只含引用，命令收到占位标记而非真实凭据。

将 `expected_boundary` 设为预期使用的 Device 设置。受限 Device 需指定精确授权。凭据注入和策略更新见[受控出站网络](../a13n-envd/egress.md)。

## 集成自己的 WebSocket Host

应用在自身生命周期内拥有监听器和 `WebSocketEnvdConnections` 实例。Envd 连接监听器后，Host 通过已接纳连接发送 EIP 请求。

1. 创建连接注册表，优先使用 `async with WebSocketEnvdConnections() as connections`。
2. 验证升级请求、协商 `eip.v1`，并从可信注册信息解析预期 Device ID。
3. 在已接纳连接的处理器中等待 `attach()`，直到连接结束：

```python
async def authenticated_envd_handler(connection):
    # Resolve this from your authenticated registration, not untrusted EIP input.
    native_id = trusted_registration.device_id
    await connections.attach(native_id, connection)
```

这个处理器片段假设监听器提供 `connection`，应用提供 `connections` 和 `trusted_registration`。附加立即执行零 Session 的 Device 握手。应在接纳时初始化，不要等到下一次执行；Envd 的初始化期限有限。

接着用该注册表构建 connector。此片段假设 `connections` 仍打开，注册的 Device ID 为 `env-remote-machine`：

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

(websocket_envd,) = select_builtin_environment_providers(("websocket_envd",))
environment = websocket_envd.execution_connector(
    {"working_directory": "/work/project"},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=websocket_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    runtime=WebSocketEnvdProviderRuntime(
        connections,
        WebSocketEnvdConnectionConfiguration(connection_timeout=30),
    ),
)
```

用 `async with await environment.open() as execution` 打开返回的 connector，或通过 Host 来源提供它。每个执行对象在共享 Device 连接上拥有独立 Session。执行完成或取消会关闭该 Session，共享连接仍可供其他执行使用。

在 Host 关闭时关闭注册表。注册表容量有限，拒绝重复的活跃 Device 连接，获取连接也有有限超时。未提供连接运行时的 WebSocket provider 会明确报告失败。

### 框架集成

直接使用 `websockets.asyncio.server.ServerConnection`，或将框架适配到 `a13n_envd_client.WebSocketConnection`：

| 成员                          | 所需行为                           |
| ----------------------------- | ---------------------------------- |
| `subprotocol`                 | 报告协商后的 `eip.v1`              |
| `recv()`                      | 返回一个完整 `str` 或 `bytes` 消息 |
| `send(message)`               | 保留文本与二进制消息的区别         |
| `close(code=1000, reason="")` | 结束已接纳连接                     |
| `wait_closed()`               | 观测关闭，不消费 EIP 消息          |

将框架断开转换为 `EOFError` 或 `OSError`。在监听器设置有限帧和队列上限，并让 EIP 独占读取已接纳消息。将连接交给 SDK 前完成身份验证；子协议和声称的 Device ID 都不能验证调用方身份。

要将示例 `run_websocket()` 监听器连接到手动运维的守护进程，先启动示例 Host：

```bash
# Start the example Host first; it waits up to 60 seconds for the daemon.
uv run environment-provider-example websocket_envd \
  --port 8788 --device-id env-remote-machine \
  --credential-file /private/envd-token
```

示例在回环连接上检查 Bearer 凭据，最多等待 60 秒。为守护进程配置 `reverse_websocket`、匹配的凭据文件和 Device ID，以及 `A13N_ENVD_REVERSE_WS_URL=ws://127.0.0.1:8788`。运维配置见 [Envd 运维指南](../a13n-envd/index.md)。部署时，用应用自己的 TLS、身份验证和路由策略替换示例监听器。

## 状态、并发与恢复

将目标身份和 Session 恢复分别保存在 Host 记录中：

| 记录                        | 用途                             | 生命周期                                                     |
| --------------------------- | -------------------------------- | ------------------------------------------------------------ |
| `EnvironmentState`          | 通过稳定 `device_id` 选择 Device | 可移植目标引用；不含端点、凭据、连接、Session 和守护进程代次 |
| `EnvdSessionRecovery.state` | 尝试附加到保留的 Session         | 只用于原逻辑执行范围的 Host 私有检查点                       |
| 进程句柄和输出观测          | 通过执行对象观测工作             | 属于该执行范围；重新打开不会还原                             |

接纳需要固定目录选择的执行前，通过已认证的 Device 信息解析省略的 cwd。Device 信息和有界目录发现不需要 Session。多个 Session 可使用同一 Device，但 Session 是资源所有者，不是租户隔离边界。

Host 执行中断后，按以下步骤恢复：

1. 从 `a13n_harness.environment.session_recovery` 创建 `EnvdSessionRecovery`。只在原逻辑范围内恢复可选 `EnvdSessionState`。
2. 将 `session_recovery=recovery` 传给 `execution_connector(...)`。执行活跃期间，将观测到的 `recovery.state` 与 `EnvironmentState` 和 Harness 状态分开保存。
3. 使用当前凭据和策略重新连接。附加检查目标、Device 身份/代次、cwd 和必需方法。提示不可用或不兼容时打开新 Session；执行边界改变时拒绝附加。认证、传输和兼容性失败直接返回。受控出站 Session 解析当前策略并打开新 Session。
4. 正常关闭执行后，将已清除的内存提示同步到 Host 持久存储。附加后正常关闭仍会关闭 Session。

消息帧传输通道丢失后，Session 分离并进入断线宽限期。恢复取决于守护进程是否仍在有效期内保留该 Session；恢复既不延长有效期，也不排斥其他已认证客户端。先检查不确定的结果，再继续操作。绝不能因断线重放可能已分派的命令或修改；准备失败后，使用新的执行尝试。

将 WebSocket 监听器和执行放在同一进程中，或显式将执行路由到连接所有者。SDK 的进程内注册表不提供跨进程中继。

## 连接 Harness UI

交互式使用时，由 Harness UI 负责注册和连接所有权：

1. 在需要使用其文件和工具的电脑上安装 `a13n-envd`。
2. 在 Harness UI 打开 **Settings → Environments → Connect Device**，复制并运行命令。
3. 保持 Envd 运行。打开打印的批准链接，登录，并在批准 Device 前核对验证码。
4. 设备上线后，为对话或 Project 选择 Device 和工作目录。仅注册不会授予执行访问权限。

第 2 步的典型命令如下：

```bash
a13n-envd connect https://your-host.example --host work --instance work
```

完整 UI 流程见 [Device 绑定指南](../a13n-harness-ui/environments-and-projects.md#add-device-bindings)。所选工作目录设置 Session 的起始位置，不会形成文件系统沙箱。

### 启用 shell 执行

默认连接允许文件操作。要运行命令，可配置显式 shell profile 或选择 Full Control。Full Control 使用当前 OS 账号权限，因此只连接可信 Host。

在 POSIX shell 中：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://your-host.example --host work --instance work
```

在 PowerShell 中：

```powershell
$env:A13N_ENVD_FULL_CONTROL = "1"
a13n-envd connect https://your-host.example --host work --instance work
```

通过 [Envd 配置](../a13n-envd/configuration.md)选择更窄的执行策略。Full Control 不提供租户隔离。

### 重启或连接另一个 Host

批准后，Envd 保存端点和受保护的附加凭据。使用相同的已存别名、实例以及显式指定过的 `--state-dir` 重启：

```bash
a13n-envd connect work --instance work
```

重连复用已批准凭据。该凭据授权 Device 附加，不授予 Host 登录或通用 API 访问；Host 只保存其摘要。

同一电脑要连接第二个 Host 时，用另一个实例启动独立进程：

```bash
a13n-envd connect https://other-host.example --host personal --instance personal
```

每个进程连接一个 Host，并拥有独立的守护进程身份、状态和凭据。停止该进程会使连接离线，文件和注册仍保留。

### 管理与撤销

要撤回访问权限，选择 **Settings → Environments → Revoke**，再选择 **Revoke connection**。撤销会阻止新访问，并在有界授权窗口内隔离活跃连接。远程文件、执行历史和已分派效果保留，撤销记录仍可见。

确实需要重新注册时，使用新的 `--instance` 并显式批准。被拒绝的凭据不会自动替换，删除本地凭据也不能接管已有注册。

### 故障排查

- 连接另一台电脑时，使用其可达的 HTTPS 主机名。`localhost` 指运行 Envd 的电脑。回环地址允许 HTTP；私有 CA 使用 `--ca-file`。
- 批准请求过期时，重新运行同一命令再次请求。撤销后认证被拒绝会使 Envd 退出，而非生成替代凭据。

## 连接 Service

使用 Service 时，先运行 HTTP 守护进程，再用端点和 token [注册为外部目标](../a13n-service/environments.md#register-an-external-target)。Service 连接目标，运维人员保留生命周期所有权。Service 不接受自助注册。
