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
2. **选择每个 Session。** 设置 `LocalEnvdEnvironmentConfiguration.working_directory`、`required_methods`，以及可选的 `egress` 和 `expected_boundary`。每个 execution 打开独立 Session。工作目录选择操作的起点，文件系统策略决定可访问的范围。

省略工作目录时使用 Device 默认值。持久保存 Run 选择的 Host 应在接纳工作前解析出显式目录。输出限制需要相互匹配：预览不超过单流上限，每个 Session 为两条流预留空间，Session spool 总量符合 Device 配额。

保持共享的 `LocalEnvdProviderRuntime` 存活，直到所有借用它的 execution 关闭，再单独关闭运行时或退出其异步上下文。execution 关闭会释放其 Session。库调用方和 Harness UI 可使用 Local Envd；Service 不提供该 provider。

## Docker

配置托管 Docker 目标时：

1. 将 `DockerConnectionConfiguration.docker_host` 设为 Host 可访问的 Engine。后续重连需访问同一 Engine。Service 的 [Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)展示了非 root 进程如何访问 socket。
2. 选择镜像和执行路径。Service 的 `DockerRecipe` 默认采用 `execution="native"` 和 `ghcr.io/converge-ai-labs/a13n-sandbox:dev`。Native 执行使用 Docker exec；`execution="envd_http"` 使用镜像内的 Envd 守护进程。
3. 添加资源限制和可选绑定挂载。挂载 `source` 必须已存在于 Engine 所在机器上；`read_only` 默认为 true。保留容器自身文件系统中的 `/workspace` 和私有命令元数据。命名卷不是配方选项。
4. 创建目标、保存状态并打开 connector。拉取或重新构建镜像标签不会替换已有容器。

默认 `pull_policy="if_missing"` 只拉取缺失镜像。`"never"` 在镜像缺失时报告 `environment_image_missing`。需要精确镜像身份时使用不可变 digest。Native 执行按以下顺序选择用户：显式配方 `user`、`ai.a13n.environment.user`、镜像 USER。标准沙箱镜像选择 UID/GID 1000。

自定义 Native 镜像需要 Linux、Python 3.10+、配置的 shell，以及可写的 `/workspace` 和 `/tmp/a13n`。git-ignore 查询需要 Git。`init_script` 在新容器上运行，初始化失败不会隐式重放。它的 helper 请求超时与普通 shell 时长分开。普通变量放在 `environment`，秘密留在配方字段之外。`memory_gb` 使用十进制 GB，`cpus` 使用核数。`disable_network=true` 选择 Docker 的 `none` 网络。销毁会保留外部绑定挂载的数据。

使用 HTTP EIP 时，在连接的 `envd_http` 中配置 Worker 可达的主机和监听地址。Service 保留镜像入口，将容器 8888 端口发布到 Docker 分配的主机端口。使用兼容的 Envd 镜像并保持网络启用。镜像负责执行身份，因此该路径拒绝配方中的 `user` 覆盖。添加 `desktop: true`，可让就绪检查包含可观察、可输入的桌面。明文 HTTP 要求回环地址或显式信任的私有链路。Console 步骤见[创建 Docker 桌面](../a13n-service/environments.md#create-a-docker-desktop)。

## 云 provider

按下文选择账号和目标设置，再通过[云 Provider](providers.md#cloud-providers)比较运行时行为。[字段参考](configuration-reference.md)列出六个 provider 的 schema 和限制。创建和保留目标需要 Host 生命周期管理；构造 execution connector 不会分配云目标。

### E2B

设置 `E2BConnectionConfiguration.domain`（默认 `e2b.dev`），单独提供 `E2BCredential.api_key`。Service 的 `E2BRecipe` 默认使用模板 `base`、根目录 `/home/user`、用户 `user` 和 Python `/usr/bin/python3`，并启用互联网访问。

分别选择沙箱生命周期和请求超时。沙箱默认 3,600 秒，请求默认 30 秒；两者都不设置逐命令期限。Host 根据保留需求维护 keepalive，并在生命周期操作后保存状态。重连会选择该精确沙箱；保留输出遵循 [E2B 运行时限制](providers.md#e2b-runtime)。

Service 桌面未指定自定义模板时，使用自动私有模板准备：`template="auto"`、`execution="envd_http"` 和 `desktop_assistance=true`。首次创建构建模板，后续在同一连接和凭据范围内复用。在环境详情中跟踪实际阶段，就绪后打开**查看桌面**。只保存配置不会构建模板。见[桌面准备](providers.md#e2b-desktop-preparation)。

### Daytona

在 `DaytonaConnectionConfiguration` 中设置区域和可选组织约束，通过 `TokenCredential` 提供 API key。省略组织时从 key 识别。在 `DaytonaEnvironmentConfiguration` 中选择 snapshot，由它确定沙箱资源；工作目录为 `/workspace`。Harness 需要具体 snapshot。Service 桌面配方可在首次创建时准备 snapshot，后续复用。

### Modal

在 `ModalEnvironmentConfiguration` 中选择镜像、资源和有限生命周期。在 `ModalConnectionConfiguration` 中可选填写 workspace，并选择 App（默认 `a13n-sandboxes`）。通过 `ModalCredential` 提供 token ID 和 secret。Service 自动桌面在首次创建时准备 App 和镜像。基于文件系统快照的停止/恢复属于托管目标管理。

### Vercel Sandbox

在 `VercelConnectionConfiguration` 中设置 team 和 project，将 access token 放入 `TokenCredential`。Native 执行使用 `/vercel/sandbox`。使用 EIP 时，选择 `execution="envd_http"` 和运维构建的 `snapshot`；该路径使用 `/workspace`，且 `desktop=true` 必须使用此路径。Service 通过 `desktop_assistance=true` 添加人工访问。provider 保留命名持久沙箱，支持原生停止/恢复，并设定有限的厂商会话生命周期。参照[快照准备](providers.md#vercel-sandbox)。

### Fly.io Sprites

在 `SpritesConnectionConfiguration` 中设置组织，提供 `TokenCredential`。目标配置选择区域，默认目录为 `/home/sprite`。Sprites 自动休眠；provider 没有可调度的显式 `stop()` 操作。

Service 桌面的 `desktop=true` 会选择 `execution="envd_http"`，并在每个新 Sprite 内安装桌面。该路径使用私有 TCP 隧道，工作目录为 `/workspace`；Service 默认启用人工控制。见[自动创建 Sprites 桌面](../a13n-service/environments.md#automatic-sprites-desktops)。

### Runloop

在 `RunloopConnectionConfiguration` 中设置组织，通过 `TokenCredential` 提供 API key。在 `RunloopEnvironmentConfiguration` 中选择 blueprint、资源规格和空闲挂起间隔；根目录为 `/home/user`。

自定义客体镜像应在客体内提供配置的根目录、Python 和 shell。Daytona、Sprites 和 Runloop 使用客体 PATH 上的 `python3`。Modal 需要 Envd 镜像，并用客体 PATH 上的 `python3` 运行管理 helper；Vercel 使用其运行时 Python 路径。

## 远程 HTTP / WebSocket Envd

1. 注册或选择由外部管理的 Device。`RemoteEnvdStateData.device_id` 标识该设备。
2. 在 `RemoteEnvdEnvironmentConfiguration` 中设置 Session 工作目录、必需方法、可选 `egress` 和可选 `expected_boundary`。守护进程挂载在其运行位置配置。
3. 提供传输设置。HTTP 使用 `endpoint` 和单独的 `HttpEnvdCredential`；WebSocket 使用 Host 经过认证的 connector/runtime。
4. 打开 execution 获取 Session，使用后关闭。目标供应、停止、销毁和 keepalive 仍由外部所有者负责。

HTTP 默认初始化超时 10 秒、请求超时 30 秒，最多 32 个在途请求。明文私有链路需显式启用。WebSocket 默认连接超时 10 秒，其 Host 运行时管理连接和协议设置。这些 Provider 超时与底层客户端默认值分开。

接下来按 [Remote Envd](remote-envd.md)设置传输，或通过 [Python EIP 客户端](../a13n-envd/python-client.md)直接操作 Session。
