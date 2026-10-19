---
title: Provider 与运行时配置
sidebarTitle: Provider 与运行时
description: 注册 provider 插件，并配置每个内置 provider 的运行时、生命周期和限制。
---

目标配置描述要访问的目标；运行时持有客户端和凭据。

简要比较见[选择后端](index.md#choose-a-backend)。本页介绍目录、扩展注册和内置运行时要求。

## Provider 目录与插件

`ProviderCatalog` 是不可变的显式允许列表，由全部五个 provider 领域共用：Model、Web、Connector、Memory 和 Environment。内置环境定义按精确类型选择，已安装插件提供自己的定义：

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
catalog = ProviderCatalog(
    (
        *select_builtin_environment_providers(("direct_local", "docker")),
        *(item for plugin in plugins for item in plugin.manifest.environment),
    )
)
definition = catalog.require("acme_sandbox")
```

`load_provider_plugins()` 只导入明确列出的入口名称；选择为空时不扫描已安装元数据。它拒绝重复、格式错误、缺失或含糊的名称，并报告每个已加载插件的分发包名称、版本和导入目标作为来源信息。目录随后拒绝重复 provider 类型，因此插件不能覆盖内置定义。

显式选择插件，修改插件代码后重启 Host。

已安装分发包在共享入口组中发布一个 manifest：

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_agent_environment:manifest"
```

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

`EnvironmentProviderDefinition` 应当：

1. 声明稳定的 `type`、显示名称和可选 HTTPS 设置链接；
2. 分别声明账号配置、凭据和目标配置模型；
3. 只声明执行元数据；Service 组合独立的 `EnvironmentManagementDefinition` 提供管理；
4. 通过纯 `connector_factory` 返回固定目标的 `EnvironmentConnector`；
5. 每次 `open()` 返回新的已就绪 `EnvironmentExecution`，包含独立的 `execution_id` 和操作对象；
6. 验证固定目标引用，不修改权威状态；
7. 由 Service 或嵌入方 Host 管理目标。打开、检查就绪和关闭执行不创建、启动、替换、续期或销毁目标。

可运行的 [provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)展示单个已安装 manifest 使用相同的目录、验证、构建和 Harness 路径。共享编写契约见[插件与扩展](../a13n-harness/plugins.md#provider-plugins)。

完整 Host 侧内置生命周期，包括 Docker 状态重新进入和显式销毁，见[内置 provider 示例](examples.md)。

## 内置 provider

执行能力分为 Native 基础路径和 Envd 通用路径，资源管理按供应商保留独立实现。完整模型见[选择后端](index.md#choose-a-backend)。

| 执行路径      | Provider         | 执行能力                                             | 资源管理              |
| ------------- | ---------------- | ---------------------------------------------------- | --------------------- |
| Native        | `direct_local`   | 本地 Host 上的 Files & Shell                         | Host 管理的已有目录   |
| Native / Envd | `docker`         | Native Files & Shell；显式选择 HTTP EIP 使用通用执行 | Docker provider       |
| Native        | `e2b`            | 远程沙箱中的 Files & Shell                           | E2B provider          |
| Envd          | `local_envd`     | 通过 stdio 连接本地守护进程                          | Host 管理的守护进程   |
| Envd          | `daytona`        | 通过 EIP 连接兼容的沙箱守护进程                      | Daytona provider      |
| Envd          | `modal`          | 通过 EIP 连接兼容的沙箱守护进程                      | Modal provider        |
| Envd          | `vercel`         | 通过 EIP 连接兼容的沙箱守护进程                      | Vercel provider       |
| Envd          | `sprites`        | 通过 EIP 连接兼容的沙箱守护进程                      | Sprites provider      |
| Envd          | `runloop`        | 通过 EIP 连接兼容的沙箱守护进程                      | Runloop provider      |
| Envd          | `http_envd`      | HTTP(S) EIP                                          | 仅连接                |
| Envd          | `websocket_envd` | 反向 WebSocket EIP                                   | 仅连接；Host 管理入口 |

Native Local、Docker、E2B 直接提供 Files & Shell。Daytona、Modal、Vercel、Sprites、Runloop 的通用执行统一使用 Envd/EIP；各自的资源管理 provider 继续使用厂商 API。Docker 保留显式的 Native 和 Envd 路径。

部署 Envd 前，检查[沙箱验证记录](../a13n-envd/egress.md#cloud-platform-validation-record)中的启动身份、出站网络覆盖和平台限制。执行能力由守护进程协商结果与 Host 策略决定。

多租户授权和容器分配仍由 Host 负责。一个设备支持多个并发独立会话；会话不是租户分区。Host 共享设备连接，为每个适配器选择固定 cwd，只有 Host 关闭时才关闭共享运行时。

从[内置示例](examples.md)或[本地运行两种远程传输](remote-envd.md)开始。HTTP/WebSocket provider 要求 Host 提供指明设备的 `EnvironmentState`，只提供执行定义，不供应或销毁基础设施。WebSocket SDK 接收 Host 提供的已验证连接，绝不打开监听器。

## Local Envd 运行时

Host 选择一个兼容的 `a13n-envd` 可执行文件和私有运行时分配器：

```python
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

`resolve_a13n_envd_executable()` 依次检查显式参数、`A13N_ENVD_EXECUTABLE`，以及 `PATH` 上的 `a13n-envd` 或 `a13n-envd.exe`。库不加载 `.env`、安装原生二进制文件或悄悄回退到 Direct Local。首次获取设备时验证守护进程与客户端的精确兼容性。运行时延迟启动一个共享设备；每个适配器打开自己的会话。使用后关闭适配器，Host 关闭时调用 `await runtime.close()`。没有原生隔离探测。Envd 实施你选择的启动 Sandbox 和出站网络模式；Host 的账号、容器或虚拟机决定外层边界。

通过 `make local-envd-test` 运行真实 provider 路径，或参考 [Local Envd 示例](examples.md#local-envd)。

## Docker 运行时

Docker 使用 Host 进程的 Engine 连接。默认原生路线通过 Docker exec 执行；HTTP EIP 路线还连接镜像管理的 Envd 守护进程。原生运行时可借用 Engine 客户端：

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

原生 provider 覆盖镜像入口，启用 Docker init 支持，并在执行之间保持容器存活。私有工作目录为 `/workspace`。可选绑定挂载将 Docker Engine 所在机器上的已有目录开放到显式容器目标；删除时保留外部数据。不支持命名卷配置。镜像仓库身份验证、凭据 helper、镜像站和代理仍由 Docker 客户端配置。

`close()` 断开本地观测，不停止容器或后台进程。新的托管适配器复用已保存容器；确认不存在时打开失败，由管理方决定后续操作，不会自动创建替代容器。传输失败不能证明目标不存在。Docker 文件路径为原生容器路径，Harness 添加聚合挂载前缀；相对工具路径从 `/workspace` 开始。

Docker 目标配置接受 `pull_policy="if_missing"`（默认）或 `"never"`。`never` 对缺失镜像返回 `environment_image_missing`，绝不联系镜像仓库。使用 `make image-sandbox` 构建镜像，直接 provider 使用见 [Docker 生命周期示例](examples.md#docker)。Service 从模板管理 Docker 环境；参见 [Service 环境](../a13n-service/environments.md)。

使用 `execution="envd_http"` 时，connector 向保留的容器打开 HTTP EIP Session。关闭 Session 会保留桌面；停止并重启容器会保留文件，但重建图形会话状态。独立 HTTP 路由和兼容镜像见 [Docker 配置](configuration.md#docker)。

Native Docker 提供 Files & Shell，包括进程控制和输出观察。端口探测／等待和 Computer 操作需要 `execution="envd_http"`；原来使用原生端口操作的调用方需要显式选择兼容的 Envd 目标。现有原生默认值和目标指纹保持不变。修改已有原生容器的配置并不代表其中已安装守护进程。

## 云 provider

E2B、Daytona、Modal、Vercel Sandbox、Fly.io Sprites 和 Runloop 各自保留资源管理 provider。E2B 提供 Native Files & Shell；其余五种使用通用 Envd 执行路径。E2B 目标需要进阶能力时，也通过兼容的 Envd 连接使用这些能力。Service 的 E2B 账号通过域名选择 E2B 云或其他兼容服务，见 [Service 环境](../a13n-service/environments.md#providers)。

| Provider       | 后端设置                                                              | 凭据字段                         | 常用目标配置设置                                                              |
| -------------- | --------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------------- |
| E2B            | `domain`、可选 `api_url`                                              | `api_key`                        | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`        |
| Daytona        | `organization_id`、`target`（默认 `us`）                              | `api_key`                        | `snapshot`，决定沙箱资源                                                      |
| Modal          | `workspace`、已有已部署 `app_name`、`environment_name`（默认 `main`） | `token_id`, `token_secret`       | `image`（默认 `python:3.13-slim`）、`cpu`、`memory`（MiB）、`timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                               | `api_key`（Vercel access token） | `runtime`（默认 `python3.13`）、`vcpus`、`timeout_seconds`                    |
| Fly.io Sprites | `organization`                                                        | `api_key`（Sprites token）       | `region`                                                                      |
| Runloop        | `organization`                                                        | `api_key`                        | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                       |

使用拥有所提供凭据的组织、团队或 Modal `workspace`；这些字段描述后端命名空间，不授予权限。凭据绝不属于目标配置或重连状态。

Daytona、Modal、Vercel、Sprites 和 Runloop 的管理 provider 负责准备目标、兼容的 Envd 运行时和授权连接。镜像和快照可预装守护进程。文件路径、命令、进程和输出语义由 EIP 提供；OS sandbox 和 Egress 按 Host 选择的策略执行。能力以实际协商结果为准。现有目标引用与配置的迁移必须显式进行，不能将旧的原生执行配置静默解释为 Envd 配置。

### 重连与生命周期

Host 保存最新 `EnvironmentState` 并将其交给固定目标连接配置。关闭执行只释放自己的资源。已知目标丢失时不会自动重建；管理端必须明确决定后续操作。超时、权限失败和未知响应都不等于目标不存在。

| Provider       | 显式停止/恢复                           | 内存             | 过期与保留                                                                                               |
| -------------- | --------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------- |
| E2B            | 原生暂停/恢复保留文件                   | 原生暂停保留内存 | 整个沙箱 TTL 可续期；保活报告观测到的过期时间，不恢复已暂停沙箱。                                        |
| Daytona        | 原生停止/启动，包括已归档沙箱           | 不承诺保留       | 请求禁用自动停止/删除，且无硬 TTL。组织强制硬 TTL 会在就绪检查时被拒绝。                                 |
| Modal          | 文件系统快照后终止；恢复从快照创建沙箱  | 不保留           | 运行中沙箱最多固定 24 小时。保活报告保守的已知期限，拒绝承诺延期。停止快照不过期，成功恢复或销毁后删除。 |
| Vercel Sandbox | 命名原生沙箱停止/恢复，保留文件系统     | 不保留           | 运行会话有有限超时。续期验证返回期限并遵循配置总上限。原生停止快照配置为不过期。                         |
| Fly.io Sprites | 无显式停止 API；空闲时休眠，exec 时唤醒 | 不承诺保留       | 持久文件系统在原生自动休眠后保留。该 provider 不支持 `stop()`，因此不要调度显式停止；销毁是独立操作。    |
| Runloop        | 原生挂起/恢复保留磁盘                   | 不保留           | 空闲策略挂起 Devbox。保活确认新的空闲间隔，不能启动已挂起目标。                                          |

Modal 的停止会先保存文件系统快照，显式 `start()` 才从该快照恢复目标，并返回恢复后的引用。`open()` 不执行恢复或快照清理。快照删除需要匹配原生所有权标签。外部注册只提供连接配置；销毁权限属于独立管理端，不能从状态引用推导。

创建中断后，管理 provider 通过原生名称或归属元数据核对目标。命令派发结果未知时不自动重放。Envd 路径统一使用 EIP 的命令期限、取消、输出和 Session 清理语义；资源停止与销毁仍由各供应商的管理 provider 执行。

### E2B 运行时

E2B 通过原生异步 SDK 直接执行命令。有界 Python helper 实现文件操作。默认 `base` 模板无需安装 `a13n-envd`、上传可执行文件或构建自定义模板。自定义模板需要 Linux、Python 3.11+、Bash，以及配置的账号/根目录；git-ignore 查询还需要 Git。

此处 `PreparedSource` 使用 [Harness 环境指南](../a13n-harness/environments.md#supply-a-source)中的 Host 来源实现。它在首次使用时返回已准备好的 connector。

```python
import os

from a13n_service.providers.environments.e2b.definition import E2B
recipe = {"template": "base", "timeout_seconds": 300}
async with await E2B.open_provider(
    configuration={"domain": "e2b.dev"}, credential={"api_key": os.environ["E2B_API_KEY"]}
) as provider:
    state = await provider.create(recipe, environment_id="env-example", operation_id="op-create")
    await state_store.publish(environment_key, state)
    connector = E2B.execution.execution_connector(
        E2B.execution_environment(recipe), credential={"api_key": os.environ["E2B_API_KEY"]},
        environment_id="env-example", state=state
    )
result = await executable.run("Inspect the sandbox", environment=PreparedSource(connector))
```

`close()` 保留沙箱和用户文件，断开当前执行的输出观测，不终止命令。管理端显式调用 `stop()`、`start()`、`keepalive()` 和 `destroy()`。打开执行通过只读查询连接已运行目标，不续期或恢复；不接受会自动恢复的 E2B 目标。库不读取 `.env`，凭据由 Host 提供。

新适配器可通过原生进程发现查找同一沙箱中仍运行的命令。这是尽力发现：沙箱 ID 不能证明某个进程存活，缺失命令绝不会自动重启。SDK 原生列表不分页；返回投影有界，但上游清单无界。

输出是 SDK 解码后的文本，不是无损原始字节。默认值区分命令并发与历史保留：

| 设置                        | 默认值  | 范围                                                    |
| --------------------------- | ------- | ------------------------------------------------------- |
| `max_active_observations`   | 128     | 活跃原生附加，包括待启动/连接；完成或断开的观测释放名额 |
| `max_observation_bytes`     | 1 MiB   | 一次观测累计 stdout/stderr 总量，包含随后被驱逐的输出   |
| `max_retained_output_bytes` | 128 MiB | 整个适配器的保留输出；优先驱逐最旧已关闭日志            |
| `timeout_seconds`           | 3600 秒 | 整个沙箱 TTL，不是命令期限                              |
| `request_timeout_seconds`   | 30 秒   | 原生请求超时，不是工具等待时间                          |

仅发现不分配输出缓冲或活跃名额。独立的 100,000 引用元数据保护要求在接纳更多引用前显式释放，不会悄悄使已完成句柄失效。内存压力下，已关闭输出可被驱逐，但引用、已知状态和累计偏移量保留。读取显式报告 `observation_evicted`、部分覆盖和剩余可用范围。活跃缓冲耗尽总预算，或单命令达到累计预算时，适配器断开该观测，不终止命令。状态查询和反复等待不能重置预算。达到上限前的临时重连把文本追加到同一日志，并标记部分覆盖；驱逐不会补充额度，而新执行会开始新观测。需要完整持久输出时，将应用日志写入文件。

E2B 使用原生登录 Bash，因此不支持直接 argv、显式非登录模式和移除环境变量。原生 stdin 有逐请求大小上限，不是跨执行配额。支持原生 kill；不支持 interrupt/terminate、进程树验证和逐命令硬期限。逐命令墙钟限制（`limits.wall_time_seconds`，或 shell 工具的 `execution_timeout_seconds`）在启动命令前被拒绝。SDK 连接/请求等待、Harness 工具等待和沙箱过期是不同限制。命令在无 observer 时运行，仍受沙箱 TTL 约束。

隔离边界是 E2B 沙箱。文件根映射不限制获准 shell 命令。不支持逐命令 CPU、内存和进程数限制。逐命令网络拒绝要求整个沙箱设置 `allow_internet_access=False`，不能追加到已启用互联网的沙箱。追加和补丁不保证并发 compare-and-swap。

在 Host 进程环境中设置 `E2B_API_KEY` 后运行示例：

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

示例在 `finally` 中显式销毁沙箱。实时集成测试需主动启用，也会销毁目标：

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### E2B 桌面准备 {#e2b-desktop-preparation}

E2B 默认使用 `execution="native"` 提供 Files & Shell，无需 Envd。使用 Computer 时选择 `execution="envd_http"` 和 `desktop=true`，并提供预装 Envd 与桌面组件的自建模板；官方 `desktop` 模板不包含 `a13n-envd`。

[模板构建脚本](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/desktop_template.py)打包兼容的 Linux amd64 Envd 二进制、公共桌面启动器、Noto 中文字体、Chromium 和桌面访问 supervisor。在源码检出中运行，先安全配置 `E2B_API_KEY`：

```bash
cd examples/environment-provider
uv sync --locked
uv run python desktop_template.py my-envd-desktop --binary /absolute/path/to/linux-amd64/a13n-envd
```

在 Console 填写模板名称或 ID。Service 创建沙箱时启动预装组件，为每个实例生成独立凭据，并通过 E2B 的 HTTPS 端口路由连接，无需另建 Envd 账号或填写公开 bearer URL。此模式下文件、Shell、Computer 操作全部走 EIP，工作目录为 `/workspace`；Native 的用户、根目录和 helper 限制仅用于 Native 路径。连接已有目标不会启动或修复组件，daemon 不可用或方法不兼容会明确报错。

需要查看和人工接管时启用 `desktop_assistance=true`。Agent 的 Computer 工具和挂载权限仍单独配置。Supervisor 执行连接授权与撤销，Service 协调安全暂停和恢复。输入、坐标转换和截图由 Envd 统一实现。目前 Linux X11 后端支持物理按键，但不支持 Unicode 文字输入，不会回退到 E2B Desktop SDK。见 [Envd Computer use](../a13n-envd/computer-use.md)。

### Daytona

Daytona 的 Agent 文件、Shell、进程、端口和 Computer 操作全部通过 Envd/EIP 执行。Provider 保留沙箱创建、检查、停止／启动、删除和私有连接配置。工作目录为 `/workspace`，绝对文件路径指向沙箱文件系统。停止／启动保留文件，不保证桌面进程或旧截图观察仍有效。停止前需确认自动删除已禁用；删除等待目标最终销毁。

无桌面和桌面环境都需要预装兼容 Envd 的自定义 snapshot。在本仓库使用 Linux amd64 二进制构建，并通过私有环境提供具有 snapshot 构建权限的 `DAYTONA_API_KEY`：

```bash
uv run --with daytona==0.225.0 python examples/environment-provider/daytona_desktop_snapshot.py my-envd --region us --binary /absolute/path/to/linux-amd64/a13n-envd
```

仅需文件和 Shell 时使用 `{"snapshot": "my-envd"}`；需要 Computer 和人工操作时使用 `{"snapshot": "my-envd", "desktop": true, "desktop_assistance": true}`。Snapshot 启动 Envd，并按配置启动共用的 X11 桌面和访问 supervisor。Service 在创建／启动后检查 EIP 就绪状态。连接或授权访问不会安装、重启或修复服务。运行沙箱的 Key 不需要 snapshot 构建权限。

Agent 的 EIP 操作通过私有 HTTP 端口 8888。Daytona 代理认证与客体 Envd 凭据独立，只保留在内存中；Host 根据部署和 Run 策略校验发现的地址，不会回退到原生执行。Agent Computer 工具和挂载权限需单独开启。Snapshot 预装中文字体；当前 Envd X11 输入支持物理按键，但不支持直接输入文字，包括中文和多行文字。

人工访问使用独立的 6081 端口短期签名预览连接。Supervisor 强制执行只读、单一控制者、可续租授权及归还时断开连接；仅撤销原生签名链接不能证明已有输入连接已终止。Console 保留查看、Agent 请求协助、获取和归还控制权交互；Service 确认执行到达安全边界后交接，归还后要求重新观察。

新建 Daytona 组织必须先在控制台选择默认区域。连接中的区域不能替代该账号设置。

`test_daytona_desktop_live.py` 验证 EIP 文件、Shell、桌面操作和停止／启动；`test_daytona_access_live.py` 验证只读访问、控制连接撤销、过期和重启隔离。这些会创建付费目标的测试与完整 Console／Agent 交接验收分别执行。

### Modal

默认镜像：`python:3.13-slim`，包含 `/usr/local/bin/python3`；根目录 `/` 对镜像 root 用户可写。使用已部署的现有 App。托管停止在终止前对文件创建快照；恢复替换原生沙箱，就绪成功后允许清理快照。运行沙箱 TTL 不能延长到已知期限之外。

### Vercel Sandbox

默认运行时：`python3.13`；根目录：`/vercel/sandbox`。命名持久沙箱使用原生停止/恢复。目标身份包含原生创建时间；仅新运行会话不会替换持久文件系统身份。删除还会请求原生异步清理孤立快照。

### Fly.io Sprites

默认根目录：`/home/sprite`；Python：客体 PATH 上的 `python3`。[Sprites 环境指南](https://docs.sprites.dev/working-with-sprites/)介绍可写 home 和预装工具。原生自动休眠保留磁盘；不支持显式停止。销毁等待确认不存在。

### Runloop

默认根目录：`/home/user`；Python：客体 PATH 上的 `python3`。Runloop 文档介绍[默认非特权用户](https://docs.runloop.ai/docs/devboxes/configuration/user-parameters)和[预装 Python 环境](https://docs.runloop.ai/docs/devboxes/overview)。保活使用返回的目标空闲间隔。清空状态前检查关闭完成。

### 嵌入式配置

使用与 Service 相同的目录和带类型的运行时构建：

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(DAYTONA,) = select_builtin_environment_providers(("daytona",))
connector = DAYTONA.execution_connector(
    {"snapshot": "my-envd"}, configuration={"organization_id": "your-organization"},
    credential={"api_key": api_key}, environment_id="env-workspace", state=saved_state,
)
async with await connector.open() as execution:
    assert execution.operations.files is not None
    listing = await execution.operations.files.list("/", max_results=20)
```

`execution_connector()` 验证输入但不获取客户端。`open()` 为每个执行获取独立客户端。借用的运行时由 Host 关闭；管理客户端和执行客户端分别拥有独立的生命周期。

### 云端验证

确定性测试覆盖目录构建、原生 HTTP 响应、Sprites WebSocket 帧，以及 Modal 真实异步 SDK 与本地 gRPC fixture 的交互，执行真实文件和 shell helper。云测试独立且需主动启用：

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

通过私有测试环境提供 `A13N_TEST_DAYTONA_BACKEND_JSON` 和 `A13N_TEST_DAYTONA_CREDENTIAL_JSON`；其他四个 provider 使用对应大写前缀。可选 `A13N_TEST_<PROVIDER>_RECIPE_JSON` 覆盖目标配置。fixture 分配计费目标，并在清理中尝试删除，包括失败后。绝不能提交凭据 JSON。未主动启用或缺少凭据时跳过，不代表完成云端验证。

原生 API 参考：[E2B](https://e2b.dev/docs)、[Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)、[Modal 快照](https://modal.com/docs/guide/sandbox-snapshots)、[Vercel Sandbox SDK](https://github.com/vercel/sandbox)、[Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/) 和 [Runloop 异步 Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html)。

Docker 模板可选择 `execution: envd_http`，并用 `desktop: true` 要求桌面就绪。容器生命周期仍使用 `docker_host`，HTTP EIP 则需单独配置可达主机和端口发布策略。启动、权限和重启行为见[桌面配置说明](../a13n-envd/sandbox.md)。
