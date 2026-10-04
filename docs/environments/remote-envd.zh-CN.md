---
title: 连接远程 Envd
sidebarTitle: 远程 Envd
description: 通过 HTTP 连接已有 Envd 守护进程，或让它通过 WebSocket 回连 Host。
---

Host 可以通过 HTTP(S) 访问已有守护进程时，使用 `http_envd`。守护进程需要回连 Host 时，例如机器位于 NAT 后方，使用 `websocket_envd`。

两者都**只负责连接**：不创建远程机器、启动守护进程、续期基础设施超时、停止守护进程或删除其文件。运维人员负责部署；Host 负责身份验证、环境选择和调度。文件、shell、进程、输出和端口使用与 Local Envd 相同的 EIP provider 操作。

## 先在本地试用两种传输

无需模型、Docker 或云账号。在仓库根目录运行：

```bash
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked

uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd

uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

每个演示启动临时本地守护进程，创建外部 provider 适配器，写入文件，关闭适配器，再通过新适配器读取文件。它验证适配器关闭后，守护进程代次和文件仍保留。最后，**演示的运维代码** 停止守护进程并删除临时文件。

预期结果包括：

```text
re-entry read: hello from remote envd
independent Sessions: True
provider close preserved remote daemon and workspace
```

演示保持命令执行禁用，使用回环连接和临时凭据，并在临时设备 cwd 中写入。它用于学习，不是生产沙箱配置。

可复制的精简 [Host 集成](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote.py)与[本地演示辅助代码](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote_demo.py)应分别阅读。生产应用通常需要前者，无需后者。

## 连接已有 HTTP 守护进程

运维人员通过受保护渠道提供 HTTP(S) origin、配置的 `A13N_ENVD_DEVICE_ID` 和凭据。origin 不含 `/eip/control` 后缀：客户端自行构造 EIP 资源路径。

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.models import EnvironmentState

(http_envd,) = select_builtin_environment_providers(("http_envd",))
state = EnvironmentState(
    provider_key=http_envd.type,
    state_version="1",
    state={"device_id": "env-remote-machine"},
)
environment = await http_envd.create(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",  # Your Host's logical identity.
    state=state,
    allow_create=False,
)

# Harness binds, uses and closes this fresh adapter for the Run.
result = await executable.run("Inspect the workspace", environment=environment)
```

必须设置 `allow_create=False`：两个远程 provider 都声明 `supports_managed=False`，并在任何外部调用前拒绝托管创建。

不用 Harness 时，按 `remote.py` 使用 `enter()`、`ensure_ready()` 和 `EnvironmentOperations`。构建和 `enter()` 不操作目标；准备阶段才连接。始终在 `finally` 关闭适配器。如果通过 `runtime=` 把同一个 `HttpEnvdProviderRuntime` 传给多个适配器，在 Host 关闭时关闭它。一个运行时可以服务多个独立适配器。

示例 CLI 也能连接已有守护进程：

```bash
uv run environment-provider-example http_envd \
  --endpoint https://envd.example.com \
  --device-id env-remote-machine \
  --credential-file /private/envd-token
```

这会在所选设备 cwd 下写入 `provider-example.txt`，再通过新会话读取。守护进程必须允许 `file.write_text` 和 `file.read_text`。绝不能通过 URL 或命令行参数提供凭据。

公共网络端点要求通过验证的 HTTPS。回环地址接受 HTTP；可信 provider 私有链路需要显式 `allow_plaintext_private_link=True`。运行时也接受通过 `verify` 提供的 SSL context、CA 文件或 `False`；除非运维人员设置 `A13N_OUTBOUND_TLS_VERIFY=false`，默认会验证证书。

## 会话出站网络与凭据引用

本地、HTTP 和 WebSocket Envd 共用只含引用的会话配置。运维人员在启动时选择设备 Sandbox 和网络模式。controlled 设备要求每个会话显式提供目标；inherit 和 deny 设备拒绝会话策略。

例如，将下列目标配置交给 provider，不要把 token 值放入配置：

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

运行时在打开会话前重新解析 `HOST_GITHUB_TOKEN`。来源缺失或为空会导致准备失败。保存的配置只含引用；描述符只含不含秘密的边界和策略元数据。本地启动会从守护进程继承的子环境排除被引用的来源变量。嵌入 Host 可以提供 `EnvdCredentialResolver` 运行时协作对象，替代进程环境来源。

`expected_boundary` 检查所选远程设备，不授予重新配置设备的权限。受限设备应指定精确规范授权目录。会话目标变化和秘密轮换不改变固定启动边界或本地守护进程缓存身份。命令接收占位标记，不接收真实凭据；请求头注入和实时策略更新见[受控出站网络](../a13n-envd/egress.md)。

## 集成自己的 WebSocket Host

连接方向是 **Envd 到 Host**；Host 仍发送所有 EIP 请求。库不打开监听器，只提供由你在应用生命周期内管理的进程内 `WebSocketEnvdConnections` 实例。

Host 验证升级并选择预期设备 ID 后：

```python
async def authenticated_envd_handler(connection):
    # Resolve this from your authenticated registration, not untrusted EIP input.
    native_id = trusted_registration.device_id
    await connections.attach(native_id, connection)
```

在 handler 整个生命周期中等待 `attach()`。SDK 立即完成零会话的设备握手，即使执行尚不存在。不要把未初始化连接排队到下次执行：Envd 的初始化期限有限。

使用其中一个连接：

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

(websocket_envd,) = select_builtin_environment_providers(("websocket_envd",))
environment = await websocket_envd.create(
    {"working_directory": "/work/project"},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=websocket_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    allow_create=False,
    runtime=WebSocketEnvdProviderRuntime(
        connections,
        WebSocketEnvdConnectionConfiguration(connection_timeout=30),
    ),
)
```

Host 直接把连接 SDK 作为运行时协作对象提供，因为已接纳连接由 Host 管理。没有连接运行时的 WebSocket provider 不会操作目标，并明确报告失败。

Host 关闭时关闭 `connections`，或使用 `async with WebSocketEnvdConnections() as connections`。每个实例具有有限连接容量，拒绝重复活跃守护进程连接，并在独立适配器会话间共享一个设备连接。等待获取有有限超时。执行完成或取消不会关闭共享连接。

### 框架集成

`websockets.asyncio.server.ServerConnection` 可直接使用。其他 Web 框架实现公开 `a13n_envd_client.WebSocketConnection` 协议：

- `subprotocol` 报告协商后的 `eip.v1`；
- `recv()` 返回一个完整 `str` 或 `bytes` 消息；
- `send(message)` 保留文本与二进制消息区别；
- `close(code=1000, reason="")` 结束已接纳连接；
- `wait_closed()` 观测关闭，不消费 EIP 消息。

将框架断开转换为 `EOFError` 或 `OSError`，在监听器保持有限帧和队列上限，并让 EIP 独占读取已接纳消息。Host **先完成身份验证**，再把连接交给 SDK。协商的子协议或设备 ID 不代表身份验证。

可运行 `run_websocket()` 示例包含小型回环 Host 监听器、Bearer token 检查和 `eip.v1` 协商。监听器是应用示例代码，不是 SDK 启动的服务器。手动运维守护进程时：

```bash
# Start the example Host first; it waits up to 60 seconds for the daemon.
uv run environment-provider-example websocket_envd \
  --port 8788 --device-id env-remote-machine \
  --credential-file /private/envd-token
```

配置守护进程使用 `reverse_websocket`、匹配凭据文件、设备 ID，以及 `A13N_ENVD_REVERSE_WS_URL=ws://127.0.0.1:8788`。完整运维配置见 [Envd 运维指南](../a13n-envd/index.md)。生产 Host 提供自己的 TLS 监听器、身份验证和路由策略。

## 状态、并发与恢复

- Host 逻辑环境 ID 可以不同于 Envd 设备 ID。初始化验证设备 ID；操作引用属于逻辑环境和当前代次。
- 只持久保存 provider 状态封装。它不含凭据、端点、连接、会话或守护进程代次。
- 一个设备接纳多个独立会话。每个新适配器以已记录 cwd 打开自己的会话并管理资源。会话不是租户隔离边界。
- 关闭适配器只关闭自身会话，不关闭远程基础设施或借用连接。HTTP 和 WebSocket 设备连接都由 Host 管理。文件连续性保留；重启守护进程会使原生句柄失效。
- 设备信息和有界目录列表不需要适配器或会话。在不可变执行接纳前，从设备信息解析省略的 cwd。
- 消息帧传输通道丢失后，会话分离并进入断线宽限期。已有所有者可显式附加到同一代次的精确同一会话；不重放操作或传输。新适配器始终打开新会话。
- 绝不能仅因连接断开就重放可能已分派的命令或修改。准备失败后，下一次尝试需要新适配器。

WebSocket SDK 只在当前进程内工作。监听器和执行 worker 位于不同进程时，Host 必须将执行路由到连接所有者，或提供显式集成。不存在默认的自动中继、分布式注册表或全局连接池。

## 连接 Harness UI

Harness UI 支持自助注册。在需要使用其文件和工具的电脑上安装 `a13n-envd`，再在 Harness UI 中从 **Settings → Environments → Connect Device** 复制命令：

```bash
a13n-envd connect https://your-host.example --host work --instance work
```

保持进程运行。打开终端打印的批准链接，登录 Host，批准前核对验证码。不要批准陌生设备或不匹配代码。仅注册不会启动对话，也不会授予执行访问设备的权限。

设备上线后，在为对话或 Project 添加环境时选择该设备和工作目录。执行尚不存在时也可发现目录。工作目录是会话起始目录，**不是** 文件系统沙箱。

### 启用 shell 执行

默认情况下，连接只允许文件操作；shell 执行需要 Full Control 或手动配置的 shell profile。要使用当前操作系统账号的完整权限：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://your-host.example --host work --instance work
```

PowerShell：

```powershell
$env:A13N_ENVD_FULL_CONTROL = "1"
a13n-envd connect https://your-host.example --host work --instance work
```

也可以提供显式 Envd 配置，选择所需执行策略。参见 [Envd 配置](../a13n-envd/configuration.md)。Full Control 不提供租户隔离；只连接可信 Host。

### 重启或连接另一个 Host

批准后，Envd 在本地保存 Host 端点和受保护、权限有限的凭据。使用保存的别名和同一实例重启：

```bash
a13n-envd connect work --instance work
```

如果提供过 `--state-dir`，也保持不变。重连不需要再次批准。秘密不是 Host 登录凭据或通用 API 凭据，Host 只保留摘要。

一个进程连接**一个 Host**。同一物理电脑需要第二个 Host 时，以不同实例运行另一个进程：

```bash
a13n-envd connect https://other-host.example --host personal --instance personal
```

各实例的守护进程身份、状态和凭据独立。单个守护进程内没有多 Host 调度器。停止进程只使连接离线，不删除文件或 Host 注册。

### 管理与撤销

Harness UI 在 **Settings → Environments** 中列出已批准设备；选择 **Revoke**，再选择 **Revoke connection**，即可永久撤销设备凭据。撤销阻止新访问，并在有界授权窗口内隔离已有连接；不会撤销已分派效果、删除文件/历史，或远程管理守护进程。撤销记录仍可见。

被撤销凭据不会悄悄替换。确实需要重新注册时，使用新的 `--instance` 并显式批准。删除本地凭据不能接管已有注册。

### 故障排查

- 除回环地址外必须使用 HTTPS。`localhost` 指运行 Envd 的电脑，不是远程 Host 电脑。访问其他电脑时使用可公开到达的主机名。私有证书颁发机构使用 `--ca-file`，不要禁用验证。
- 待批准请求过期时，重新运行同一命令再次请求。撤销后身份验证被拒绝时，Envd 退出，不生成替代凭据。

## 连接 Service

Service 不接受自助注册。运行 HTTP 守护进程，再用端点和 token [注册为外部目标](../a13n-service/environments.md#register-an-external-target)。Service 只连接，不管理生命周期。
