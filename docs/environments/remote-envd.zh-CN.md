---
title: 连接远程 Envd
sidebarTitle: 远程 Envd
description: 通过直连 HTTP(S) 或反向 WebSocket 隧道，将运行 Envd 的既有物理机、虚拟机或容器接入宿主应用。
---

按网络连通方向选择远程 `a13n-envd` 接入方式：

- **`http_envd`：** 由宿主主动连接已有的 HTTP(S) Envd 守护进程。
- **`websocket_envd`：** 由 Envd 主动向宿主的 WebSocket 监听端点发起反向连接（适合 NAT/防火墙内网机器）。

两种提供方均为“仅连接”模式——关闭执行只会分离当前 Session，不会停止远程守护进程或清理工作区文件。

## 连接到已有 HTTP 守护进程

将守护进程的 HTTPS 根地址（或本机回环 HTTP）、预期的 `device_id` 与访问令牌传给内置 `http_envd` 提供方，并用 [`PreparedSource`](../a13n-harness/environments.md#supply-a-source) 包装：

```python
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(http_envd,) = select_builtin_environment_providers(("http_envd",))
connector = http_envd.execution_connector(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=http_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
)
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

远程端点请保持 TLS 验证开启（私有 CA 可通过 `HttpEnvdProviderRuntime.verify` 传入）。

## 集成反向 WebSocket 宿主

在服务端进程内维护 `WebSocketEnvdConnections` 注册表，对入站 `eip.v1` 连接完成鉴权后构造 `websocket_envd` 连接器：

```python
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.remote_envd.configuration import WebSocketEnvdConnectionConfiguration
from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

# 1. 在已鉴权的 WebSocket 路由处理器中注册连接：
# await connections.attach(trusted_registration.device_id, connection)

# 2. 针对已注册的 device_id 构造连接器：
(websocket_envd,) = select_builtin_environment_providers(("websocket_envd",))
connector = websocket_envd.execution_connector(
    {"working_directory": "/work/project"},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=websocket_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    runtime=WebSocketEnvdProviderRuntime(
        connections, WebSocketEnvdConnectionConfiguration(connection_timeout=30)
    ),
)
```

## 会话出站与凭据引用

在连接器配方的 `session` 字段中声明受控出站策略并注入宿主侧密钥占位符（也可通过 `session.isolation` 选择虚拟机隔离）：

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
}
```

守护进程前提条件详见[受控会话出站](../a13n-envd/egress.md)与[执行隔离](../a13n-envd/isolation.md)。

## 状态、并发与恢复

- **`EnvironmentState`：** 保存可移植的 `device_id`（不含端点地址或凭据）。
- **`EnvdSessionRecovery`**（`a13n_harness.environment.session_recovery`）：在宿主侧持久化私有的 `EnvdSessionState` 检查点，使同一逻辑作用域的后续 Run 可在同一守护进程世代内重新附着到原 Session，且不会重放结果未知的命令。

## 接入 Harness UI 与 Service

- **Harness UI：** 通过 `a13n-envd connect` 配对（[连接并批准 Envd](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd)），挂载工作目录（[添加设备绑定](../a13n-harness-ui/environments-and-projects.md#add-device-bindings)），按需[启用命令](../a13n-envd/configuration.md#enable-commands)、保留[已保存的实例与宿主](../a13n-envd/configuration.md#saved-instances-and-hosts)或[撤销设备](../a13n-harness-ui/environments-and-projects.md#revoke-a-device)。
- **Service：** 使用出站模式[连接客户端计算机](../a13n-service/environments.md#connect-a-client-computer)，或将 HTTP 守护进程[注册为外部目标](../a13n-service/environments.md#register-an-external-target)。
