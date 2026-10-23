---
title: 配置 provider
description: 分别配置 provider 的目标、后端连接和凭据。
---

分别配置目标、后端连接和凭据。运行时客户端保留在内存中，保存 `EnvironmentState` 以便之后重新连接。

| 值                                | 职责                               | 示例                                 |
| --------------------------------- | ---------------------------------- | ------------------------------------ |
| 目标配置（`environment_model`）   | 该环境应开放什么                   | 根目录、Docker 挂载、E2B 模板        |
| 账号配置（`configuration_model`） | Host 从哪里连接后端                | Docker 守护进程、E2B 域名、HTTP 端点 |
| 凭据（`credential_model`）        | 当前访问后端的权限                 | E2B API key 或 HTTP EIP token        |
| 运行时协作对象                    | 活跃客户端、会话和 Host 分配的资源 | 由 Host 提供，或在显式打开时获取     |
| `EnvironmentState`                | 已验证、指向精确保留目标的引用     | 容器/沙箱身份和配置指纹              |

`management.open_provider(configuration=..., credential=...)` 获取管理客户端。`definition.execution_connector(recipe, configuration=..., credential=..., state=...)` 只验证输入并构造连接配置，`open()` 才获取执行资源。Host 也可传入 `runtime=...` 借用共享连接，此时允许提供账号配置，但不能同时提供凭据。

每个定义对每种类别只管理一个模型。没有配置 schema 版本：改变输入含义就改变了 provider 类型。

[生成的字段参考](configuration-reference.md)列出内置目标配置、账号设置、凭据、根目录、挂载和 shell 配置。

[执行模型](index.md#choose-a-backend)定义 Native Files & Shell 与 Envd 通用执行的能力边界。[配置参考](configuration-reference.md)从当前模型生成，仍包含迁移前的字段；选择路径时以执行模型为准，迁移已有配置需显式进行。

## Host 出站连接

在 Host 进程环境中设置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 或小写形式。这些可信设置路由 Host 自有 HTTP 连接，不控制设备命令流量。

TLS 默认启用验证。在构建自有 HTTP 客户端前设置 `A13N_OUTBOUND_TLS_VERIFY=false`，可关闭证书和主机名检查。私有 HTTPS 端点优先使用可信 CA。已有客户端、调用方提供的客户端及 vendor SDK/WebSocket 传输保留自有 TLS 设置。

端点和 CA 选项见 [Remote Envd](remote-envd.md#connect-to-an-existing-http-daemon)，设备命令访问目标见 [Session 出站网络](../a13n-envd/egress.md)。

## Direct Local

`DirectLocalEnvironmentConfiguration` 要求绝对路径 `root.path`。根目录始终可写；仅供参考的挂载通过 Harness 权限上限禁止写操作。基础配置仅支持文件：shell 配置和允许的可执行文件默认均为空。

要启用单个可执行文件，参考[完整命令示例](commands.md)。需要 shell 语法时，配置绝对 shell 路径和 profile ID、可选固定参数、`posix` 或 `powershell` 方言，以及显式登录许可。profile ID 必须唯一。PowerShell profile 不能启用登录模式。

`inherit_environment` 默认为 false。`allowed_environment_keys` 默认为空集合，拒绝所有显式设置或移除的键；`None` 允许任意键。请有意选择。模型编写的环境修改不能绕过该策略。使用目标前会验证绝对路径和空字符/控制字符限制。

默认限制为 64 MiB 文件值、128 个并发进程、最长 24 小时墙钟时间、五秒终止宽限、1 MiB 缓冲区和 64 GiB spool。这些是 provider 限制，不是 shell 工具展示默认值。Direct Local 无状态并共享 Host 账号；任何配置都不能将它变为 OS 沙箱。

## Local Envd

`LocalEnvdEnvironmentConfiguration` 选择可选的设备绝对路径 `working_directory`、`required_methods`、可选的会话 `egress` 和可选的 `expected_boundary`。每个适配器打开独立会话。省略目录时使用设备默认值；持久保存执行选择的 Host 在接纳工作前解析并冻结显式目录。该目录不是文件系统访问根目录。

`LocalEnvdLaunchConfiguration` 属于 Host 运行时，不属于各个适配器。它选择执行身份、文件系统 Sandbox、出站网络模式、原生默认目录、目录发现、可信可执行文件根目录、shell 配置和资源限制。shell profile 选择 `profile_id`、绝对 `executable`、固定参数、登录许可和脚本字节上限（默认 1 MiB）。可执行文件根目录和 shell 配置默认均为空。`sandbox` 和 `egress` 选择设备的文件系统 Sandbox 和网络模式，Envd 对每个会话实施这些设置；[Host 外层边界](../a13n-envd/isolation.md)仍决定可用权限。

启动默认值为 16 MiB 文件值、64 KiB 预览、每条输出流 256 MiB、每个会话 1 GiB spool 和设备总 4 GiB spool。预览不能超过单流上限；每个会话 spool 必须为两条流预留空间，并满足设备配额。

Host 管理一个共享 `LocalEnvdProviderRuntime`、其二进制文件、启动和关闭。适配器 `close()` 只关闭自身会话。请单独关闭运行时，或使用其异步上下文管理器。Local Envd 仍是库/Harness UI provider，Service 不提供它。

## Docker

使用原生执行且省略 `user` 时，provider 使用镜像标签 `ai.a13n.environment.user`；没有该标签的自定义镜像沿用镜像 USER。`a13n-sandbox` 选择 `sandbox`（UID/GID 1000）；recipe 显式设置的 `user` 优先。

Service 的 `DockerRecipe` 默认采用 `execution="native"`，通过 Docker exec 提供 Files & Shell。选择 `execution="envd_http"` 可通过 HTTP EIP 使用镜像管理的 Envd 守护进程。默认镜像是 `ghcr.io/converge-ai-labs/a13n-sandbox:dev`；需要精确镜像身份时选择不可变 digest。存在本地镜像时直接使用。默认 `pull_policy="if_missing"` 时拉取缺失镜像；设为 `"never"` 时以 `environment_image_missing` 失败。重新构建或拉取标签不会重建已有环境容器。

私有容器文件系统提供 `/workspace`。可选绑定挂载包含 Docker Engine 所在机器上已存在的绝对 `source`、容器 `target` 和 `read_only` 标志（默认 true）。不能替换 `/workspace` 或私有命令元数据。命名卷不是目标配置选项；销毁时保留外部数据。

`cpus` 以 CPU 核数计；`memory_gb` 使用十进制 GB（1 GB = 1,000,000,000 字节），最低 6 MiB（0.006291456 GB）；`pids_limit` 限制进程数。`environment` 设置普通变量。`init_script` 只在新容器运行，初始化失败不隐式重放。不要在这些目标配置字段中保存秘密。`disable_network` 默认关闭，启用时选择 Docker 的 `none` 网络。

原生执行选项包括用户、shell、Python 可执行文件、停止宽限（十秒）、逐 helper 请求超时（60 秒；包含初始化脚本，不限制 agent 执行或普通 shell 时长）、文件值（16 MiB）、输出预览（64 KiB）、每流捕获字节（16 MiB）、总观测/保留预算（各 64 MiB）和并发进程观测（128）。自定义镜像需要 Linux、Python 3.10+、配置的 shell、可写 `/workspace` 和 `/tmp/a13n`；只有 git-ignore 查询需要 Git。

`DockerConnectionConfiguration.docker_host` 选择 Engine socket。重启后，Host 进程必须连接同一 Engine 才能重新进入容器。Service 的 [Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)挂载 Engine socket，并授予非 root Service 进程访问权限。

使用 HTTP EIP 时，在 Docker 连接的 `envd_http` 中配置 Worker 可达的主机和监听地址。Service 保留镜像入口，将容器 8888 端口发布到 Docker 分配的主机端口。使用兼容的 Envd 镜像；需要就绪检查包含可观察、可输入的桌面时，设置 `desktop: true`。镜像管理守护进程和 Session 身份，因此拒绝 recipe 的 `user` 覆盖。HTTP 执行需要网络；明文 HTTP 使用回环地址或显式信任的私有链路。Console 操作见[创建 Docker 桌面](../a13n-service/environments.md#create-a-docker-desktop)。

## 云 provider

E2B、Daytona、Modal、Vercel Sandbox、Fly.io Sprites 和 Runloop 使用相同的后端/凭据/目标配置分离方式。六者的说明见[配置比较](providers.md#cloud-providers)和[生成的 schema 参考](configuration-reference.md)。

### E2B

Service 的 `E2BRecipe` 默认使用模板 `base`、根目录 `/home/user`、用户 `user` 和 Python `/usr/bin/python3`；路径必须为绝对路径且不能包含路径穿越。默认允许互联网访问。

沙箱超时默认 3,600 秒（30–86,400）；请求超时默认 30 秒（最多 300）。两者都不是逐命令执行期限。文件值默认 100 MiB（最多可配置为 1 GiB）、观测字节 1 MiB、活跃观测 128、保留输出总量 128 MiB、查询条目 10,000。[运行时限制](providers.md#e2b-runtime)介绍基于文本的观测和支持的命令控制。

`E2BConnectionConfiguration.domain` 默认为 `e2b.dev`；`E2BCredential.api_key` 是单独保护的值。Host 提供运行时凭据，在保留策略需要时维护保活，并在生命周期操作后持久保存沙箱状态。新适配器可重连精确保留的沙箱，但不会重建丢失输出或扩大访问。

在 Service/Console 中，勾选桌面且不指定自定义模板时，默认自动准备私有模板（`template="auto"`、`execution="envd_http"`、`desktop_assistance=true`）。首次创建构建，后续在同一连接和凭据范围内复用。见[桌面准备](providers.md#e2b-desktop-preparation)。

首次自动创建 E2B 桌面通常需要 1～2 分钟，也可能更久；后续创建复用模板。创建后，环境详情显示实际准备阶段和已等待时间。可以离开页面，后台会继续处理，稍后从环境实例列表重新打开详情。就绪后点击“查看桌面”获取只读快照，按需刷新；交互操作仍从会话中接管。创建失败会展示原因，上一轮操作结果确认后可以“重试创建”。只保存配置不会提前构建。

### Daytona

`DaytonaEnvironmentConfiguration` 选择决定沙箱资源的快照。工作目录为 `/workspace`。`DaytonaConnectionConfiguration` 提供可选组织约束和目标区域；`TokenCredential` 提供 API key，省略组织时从 Key 识别。Harness 需要具体快照；Service 桌面配方可以省略快照，首次创建自动构建、后续复用。

### Modal

`ModalEnvironmentConfiguration` 选择镜像、资源和有限生命周期。`ModalConnectionConfiguration` 可选填写 Modal `workspace`，并指定 App（默认 `a13n-sandboxes`）；Service 自动桌面在首次创建时准备 App 和镜像；`ModalCredential` 包含 token ID 和 secret。基于文件系统快照的停止/恢复仅用于托管目标。

### Vercel Sandbox

`VercelEnvironmentConfiguration` 默认提供原生 Files & Shell，根目录为 `/vercel/sandbox`。选择 `execution="envd_http"` 和运维构建的 `snapshot`，即可通过 EIP 使用 `/workspace`；`desktop=true` 必须使用该路径。Service 另以 `desktop_assistance=true` 开启人工访问。`VercelConnectionConfiguration` 指定 team 和 project；`TokenCredential` 保存 Vercel access token。Provider 使用命名持久沙箱，保留原生停止／恢复和会话时限。参见[快照准备](providers.md#vercel-sandbox)。

### Fly.io Sprites

`SpritesEnvironmentConfiguration` 选择区域，默认目录为 `/home/sprite`。`SpritesConnectionConfiguration` 指定组织；`TokenCredential` 保存 Sprites token。不要调度显式停止：Sprites provider 不支持 `stop()`，且 Sprites 会自动休眠。

Service 中设置 `desktop=true` 会自动选择 `execution="envd_http"`，并在每个新 Sprite 内安装桌面。执行通过私有 TCP 隧道连接 Envd，工作目录为 `/workspace`，默认启用人工接管。参见[自动创建 Sprites 桌面](../a13n-service/environments.md#automatic-sprites-desktops)。

### Runloop

`RunloopEnvironmentConfiguration` 选择 blueprint、资源规格和空闲挂起间隔，根目录为 `/home/user`。`RunloopConnectionConfiguration` 指定组织；`TokenCredential` 提供 API key。

Daytona、Sprites 和 Runloop 使用客体 PATH 上的 `python3`。Modal 要求 Envd 镜像，仅在管理 helper 中使用客体 PATH 上的 `python3`；Vercel 使用其运行时 Python 路径。自定义客体镜像必须提供配置的根目录、Python 和 shell；这些路径不指向 Host 文件系统。共享命令/文件限制列于各自生成的目标配置 schema。

## 远程 HTTP / WebSocket Envd

两个远程 provider 都使用 `RemoteEnvdEnvironmentConfiguration` 作为目标配置；它选择固定会话工作目录、必需方法、可选的会话 `egress` 和可选的 `expected_boundary`，不配置守护进程挂载或供应目标。必需方法名会规范化并去重。`RemoteEnvdStateData.device_id` 选择由外部管理的设备。

HTTP 后端配置提供 `endpoint`、初始化超时（10 秒）、请求超时（30 秒）、最大在途请求（32），以及显式允许明文私有链路的选项（默认 false）。token 由单独的 `HttpEnvdCredential` 提供。远程 provider 的请求超时不同于底层 Python EIP 客户端默认值；请明确当前配置的是哪条边界。

WebSocket 后端配置具有十秒连接超时。Host connector/运行时提供经过身份验证的连接和协议设置；不能把 URL 或 WebSocket 传输对象放进目标配置或账号配置来绕过该流程。

远程 Envd provider 连接外部管理的目标，不承担托管供应、停止、销毁或目标保活。完整传输设置见 [Remote Envd](remote-envd.md)，底层会话操作见 [Python EIP 客户端](../a13n-envd/python-client.md)。
