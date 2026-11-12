---
title: 配置 provider
description: 选择目标、提供连接设置，并使用当前凭据打开执行。
---

先选择[后端](index.md#choose-a-backend)，再分别准备目标输入、账号设置和凭据：

| 输入                              | 填写内容                    | 示例                               |
| --------------------------------- | --------------------------- | ---------------------------------- |
| 目标配置（`environment_model`）   | 工作区和执行设置            | 根目录、容器挂载、必需方法         |
| 账号配置（`configuration_model`） | 后端地址和账号选择          | Docker Engine、E2B 域名、HTTP 端点 |
| 凭据（`credential_model`）        | 当前访问密钥                | API key 或 HTTP EIP token          |
| 运行时协作对象                    | Host 管理的活跃连接或运行时 | 共享 Local Envd Device             |
| `EnvironmentState`                | 指向选定目标的已保存引用    | 容器或沙箱身份                     |

调用 `definition.execution_connector(recipe, configuration=..., credential=..., state=...)` 验证输入并构造 connector。准备获取执行资源时，再调用 `open()`。借用 Host 管理的连接时，传入 `runtime=...` 和账号设置，省略 `credential`。

创建或启动托管目标是单独的步骤：`management.open_provider(configuration=..., credential=...)` 获取管理客户端。具体流程见[生命周期与状态](lifecycle.md)。保存目标状态供后续连接使用，活跃客户端留在内存中。要继续已有 Envd Session，使用独立的 [Session 恢复提示](remote-envd.md#state-concurrency-and-recovery)。

以下各节解释不同后端的配置选择。完整 schema、默认值和数值限制见[生成的字段参考](configuration-reference.md)。

## Host 出站连接

需要 HTTP 代理时，在 Host 进程环境中设置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 或 `NO_PROXY`，也支持小写形式。这些设置路由 Host 自有的 HTTP 连接。Device 上执行的命令需单独配置 [Session 出站网络](../a13n-envd/egress.md)。

保持 TLS 验证启用，为私有 HTTPS 端点使用可信 CA。在构建自有 HTTP 客户端前设置 `A13N_OUTBOUND_TLS_VERIFY=false` 会关闭证书和主机名检查。已有客户端、调用方提供的客户端、厂商 SDK 和 WebSocket 传输保留各自的 TLS 设置。端点和 CA 配置见 [Remote Envd](remote-envd.md#connect-to-an-existing-http-daemon)。

## Direct Local

1. 按[快速入门](getting-started.md)，将 `root.path` 设为已有 Host 目录的绝对路径。
2. 保留默认的仅文件配置，或按[命令示例](commands.md)添加允许的可执行文件。
3. 需要 shell 语法时，添加 shell profile：填写唯一 ID、绝对可执行文件路径、所需固定参数，以及 `posix` 或 `powershell` 方言。支持的 POSIX shell 需显式允许登录模式；PowerShell profile 不支持登录模式。
4. 选择环境变量访问范围。`inherit_environment` 默认为 false。空的 `allowed_environment_keys` 拒绝所有显式设置或移除的键；列出允许的键，或使用 `None` 允许任意键。

根目录配置允许写入。要通过 Harness 挂载仅供参考的目录，在挂载权限上限中去掉写操作。Direct Local 以 Host 账号运行，不提供 OS 沙箱。它无状态，因此 connector 使用 `state=None`。

Provider 的资源限制控制文件值、并发进程、命令墙钟时间和保留输出，与工具显示限制分开。默认值见[字段参考](configuration-reference.md)。

## Local Envd

分两层配置 Local Envd：

1. **启动 Device。** 为 Host 运行时提供 `LocalEnvdLaunchConfiguration`：执行身份、文件系统沙箱、网络模式、默认目录、可执行文件根目录、shell profile 和资源限制。可执行文件根目录和 shell profile 初始为空。[Host 边界](../a13n-envd/isolation.md)决定可用的隔离能力。
2. **选择 Session 和目录视图。** 将启动策略放在 `LocalEnvdEnvironmentConfiguration.session`：工作目录、sandbox、`egress_mode`、`egress`、生命周期和命令选择。外层 `working_directory` 选择目录视图；`required_methods` 和 `expected_boundary` 验证实际执行环境。共享 Host Session 所有者可让多个视图借用同一 Session。文件系统策略决定 worker 可访问的范围，视图 cwd 只选择操作起点。

省略工作目录时使用 Device 默认值。持久保存 Run 选择的 Host 应在接纳工作前解析出显式目录。输出限制需要相互匹配：预览不超过单流上限，每个 Session 为两条流预留空间，Session spool 总量符合 Device 配额。

保持共享的 `LocalEnvdProviderRuntime` 存活，直到所有借用它的 execution 关闭，再单独关闭运行时或退出其异步上下文。execution 关闭会释放其借用；共享 Session 所有者在最后一次释放时分离连接并保留恢复状态。库调用方和 Harness UI 可使用 Local Envd；Service 不提供该 provider。

## Docker

Docker 目标配置、镜像要求和共享 Engine 客户端集中在 [Docker 运行时](providers.md#docker-runtime)。使用字段参考中的 `DockerRecipe` 管理目标，使用 `DockerEnvironmentConfiguration` 连接已选目标。

## 云 provider

账号、凭据、运行时差异和桌面准备集中在[云 Provider](cloud-providers.md)。[字段参考](configuration-reference.md#cloud-providers)分别列出 Harness 固定目标输入和 Service 管理 recipe；后者可以准备自动镜像或快照。构造 connector 不会创建收费目标。

### E2B

设置步骤和后端限制见[E2B](cloud-providers.md#e2b-runtime)。

### Daytona

设置步骤和后端限制见[Daytona](cloud-providers.md#daytona)。

### Modal

设置步骤和后端限制见[Modal](cloud-providers.md#modal)。

### Vercel Sandbox

设置步骤和后端限制见[Vercel Sandbox](cloud-providers.md#vercel-sandbox)。

### Fly.io Sprites

设置步骤和后端限制见[Fly.io Sprites](cloud-providers.md#flyio-sprites)。

### Runloop

设置步骤和后端限制见[Runloop](cloud-providers.md#runloop)。

### boxd

设置步骤和后端限制见[boxd](cloud-providers.md#boxd)。

## 远程 HTTP / WebSocket Envd

1. 注册或选择由外部管理的 Device。`RemoteEnvdStateData.device_id` 标识该设备。
2. 在 `RemoteEnvdEnvironmentConfiguration.session` 下设置启动策略，包括可选 `egress_mode` 和只含引用的 `egress`。外层 `working_directory` 选择目录视图，另设 `required_methods` 和可选 `expected_boundary`。Session 授权必须符合 daemon 部署上限。
3. 提供传输设置。HTTP 使用 `endpoint` 和单独的 `HttpEnvdCredential`；WebSocket 使用 Host 经过认证的 connector/runtime。
4. 打开 execution 获取 Session，使用后关闭。目标供应、停止、销毁和 keepalive 仍由外部所有者负责。

HTTP 默认初始化超时 10 秒、请求超时 30 秒，最多 32 个在途请求。明文私有链路需显式启用。WebSocket 默认连接超时 10 秒，其 Host 运行时管理连接和协议设置。这些 Provider 超时与底层客户端默认值分开。

接下来按 [Remote Envd](remote-envd.md)设置传输，或通过 [Python EIP 客户端](../a13n-envd/python-client.md)直接操作 Session。
