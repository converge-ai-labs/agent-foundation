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

在 `session` 下选择不可变启动策略，权限受 Device 部署上限约束。受控 Session 必须显式提供目标；继承和拒绝网络的 Session 不接受目标策略。本地、HTTP 和 WebSocket Envd 使用相同的只含引用的配置。

对于以禁用 Sandbox、受控出站网络模式启动的 Device，以下配置允许 GitHub API 流量，并指定 Host 侧凭据来源：

```python
recipe = {
    "working_directory": "/work/project",
    "session": {
        "egress_mode": "controlled",
        "egress": {
            "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
            "secrets": [{
                "env": "GH_TOKEN",
                "source": {"kind": "environment", "name": "HOST_GITHUB_TOKEN"},
                "inject_hosts": ["api.github.com"],
            }],
        },
    },
    "expected_boundary": {
        "sandbox": {"mode": "disabled"},
        "egress": "controlled",
    },
}
```

在 Host 环境中设置 `HOST_GITHUB_TOKEN`，再将 `recipe` 作为第一个参数传给 `execution_connector`。运行时在打开每个 Session 前解析来源，值缺失或为空会导致准备失败。保存的配置只含引用，命令收到占位标记而非真实凭据。

将 `expected_boundary` 设为要求的实际 Session 边界。受限 Session 需指定精确授权。凭据注入和策略更新见[受控出站网络](../a13n-envd/egress.md)。

对于 VM Session，将 `session.isolation` 设为 Envd VM 配置，并为 `session.working_directory` 和每个目录视图的 `working_directory` 使用 guest 路径。例如：

```python
recipe = {
    "working_directory": "/workspace/project",
    "session": {
        "working_directory": "/workspace",
        "isolation": {
            "mode": "vm",
            "backend": "boxlite",
            "image": "mirror.gcr.io/library/ubuntu:24.04",
            "cpus": 1,
            "memory_mib": 1024,
            "mounts": [{
                "source": "/srv/workspace",
                "target": "/workspace",
                "access": "read_write",
            }],
        },
    },
}
```

挂载源属于 Device 宿主机，目标属于 guest。打开此配置前先创建 `/srv/workspace/project`。Device 必须包含所选 VM 后端并允许请求的挂载；也支持 `microsandbox`。VM Session 当前使用继承网络和禁用原生 Sandbox 的策略，详见 [VM 隔离](../a13n-envd/isolation.md)。Harness UI 通过显式 Device 绑定接受此配置。本地 Project 适配器保留 Host 路径，不会将 Project 和 Thread 文件路径映射到 guest，因此拒绝 VM profile。

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

用 `async with await environment.open() as execution` 打开返回的 connector，或通过 Host 来源提供它。未提供 Host Session 所有者时，每个执行对象在共享 Device 连接上独立拥有并关闭 Session。要让目录视图和多次 Run 共享 Session，按下文提供同一个 `EnvdSessionRecovery` 所有者。

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

对于原生 Session，接纳需要固定目录选择的执行前，通过已认证的 Device 信息解析省略的 cwd。Device 信息和有界目录发现不需要 Session。VM Session 需要 guest cwd。多个 Session 可使用同一 Device；实际隔离取决于所选 Session 边界，而不是不同的 Session 身份。

Host 执行中断后，按以下步骤恢复：

1. 从 `a13n_harness.environment.session_recovery` 创建 `EnvdSessionRecovery`。只在原逻辑范围内恢复可选 `EnvdSessionState`。
2. 将 `session_recovery=recovery` 传给 `execution_connector(...)`。执行活跃期间，将观测到的 `recovery.state` 与 `EnvironmentState` 和 Harness 状态分开保存。
3. 使用当前凭据重新连接。附加验证逻辑目标、Device 代次、所选 Session 配置和实际描述符。普通新工作可在确认原 Session 丢失后新建；认证、传输和协议错误直接返回。启动配置变化时，必须先显式退役，再打开后继 Session。关闭旧 Session 前先持久化退役状态；关闭结果不确定时保留其描述符。
4. 为绑定的全部目录视图传入同一个所有者。最后一个执行对象关闭时只分离连接，保留检查点供后续 Run 使用。视图工作目录不划分 Session 身份。
5. 恢复保留的原生工作时使用 `recovery.exact(original_session_id)`。该借用者必须恢复原 Session，绝不新建替代。绑定已接纳的工作全部结束后才可退役；已退役的配置不会复活。

消息帧传输通道丢失后，Session 分离，原有的空闲期限和最大生命周期保持不变。恢复要求守护进程仍保留同一代次中的同一 Session。重新附加会推进 owner epoch，使旧所有者的请求失效；这不构成已认证客户端之间的授权边界。先检查不确定的结果，再继续操作。绝不能因断线重放可能已分派的命令或修改；准备失败后，使用新的执行尝试。

将 WebSocket 监听器和执行放在同一进程中，或显式将执行路由到连接所有者。SDK 的进程内注册表不提供跨进程中继。

## 连接 Harness UI

按 Harness UI Device 绑定流程完成配对、选择工作目录并为 Thread 授权。注册本身不授予 Run 访问权限。 [操作步骤](../a13n-harness-ui/environments-and-projects.md#add-device-bindings)。

### 启用 shell 执行

默认守护进程连接只开放文件操作。按守护进程配置选择显式 shell profile 或 Full Control；后者使用所选账号权限，不提供租户隔离。 [操作步骤](../a13n-envd/configuration.md#enable-commands)。

### 重启或连接另一个 Host

重启时保留原 instance、Host 别名、状态目录与启动参数。连接另一个 Host 使用独立进程和 instance。 [操作步骤](../a13n-envd/configuration.md#saved-instances-and-hosts)。

### 管理与撤销

用撤销操作解除信任；遗忘本地配置是另一项操作。远程文件与已经派发的效果保留。 [操作步骤](../a13n-harness-ui/environments-and-projects.md#revoke-a-device)。

### 故障排查

检查可达 HTTPS 地址、Device 批准与保存的凭据。已撤销凭据不会自动替换。 [操作步骤](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd)。

## 连接 Service

使用 Service 时，先运行 HTTP 守护进程，再用端点和 token [注册为外部目标](../a13n-service/environments.md#register-an-external-target)。Service 连接目标，运维人员保留生命周期所有权。Service 不接受自助注册。
