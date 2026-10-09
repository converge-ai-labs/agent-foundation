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

1. 声明匹配 `^[a-z][a-z0-9_]{0,63}$` 的稳定 `type`、`display_name`，以及可选 HTTPS `setup_url` 和 `setup_label`；
2. 声明用于账号输入的 `configuration_model`、可选 `credential_model`，以及用于期望目标配置的 `environment_model`；
3. 在 `runtime_factory(configuration=..., credential=...)` 中获取共享协作对象，或将操作连接和会话延迟到准备阶段；绝不能在导入或验证时进行 I/O；
4. 从 `construct(configuration=recipe, environment_id=..., state=..., runtime=..., operation_id=..., allow_create=...)` 返回一个尚未操作目标的新 `Environment`；逐适配器创建策略和操作身份不能放入可复用运行时，`describe_environment()` 应无需目标 I/O 就能反映配置能力；
5. 修改前验证提供的状态，每次目标身份转换都更新缓存状态；
6. 进入后提供与 provider 无关的 `EnvironmentOperations`；
7. 如实声明 `supports_managed`、`supports_stop`、`supports_destroy` 和 `requires_keepalive`，保持 `close()` 不销毁目标，只有显式 `destroy()` 才移除目标。

可运行的 [provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)展示单个已安装 manifest 使用相同的目录、验证、构建和 Harness 路径。共享编写契约见[插件与扩展](../a13n-harness/plugins.md#provider-plugins)。

完整 Host 侧内置生命周期，包括 Docker 状态重新进入和显式销毁，见[内置 provider 示例](examples.md)。

## 内置 provider

操作有两条路线：**Native** 直接使用宿主 OS 或厂商 API；**Envd** 在不同部署和连接方式下复用同一套 EIP 操作实现。

| 路线   | Provider         | 适用场景                 | 操作与归属边界                                 |
| ------ | ---------------- | ------------------------ | ---------------------------------------------- |
| Native | `direct_local`   | 可信本地自动化           | 宿主 OS 操作；已有目录，不承诺沙箱隔离         |
| Native | `e2b`            | 原生托管云沙箱           | E2B SDK；创建、暂停、恢复、续期和销毁沙箱      |
| Native | `daytona`        | 云沙箱                   | 原生停止/启动并保留文件                        |
| Native | `modal`          | 云沙箱                   | 基于快照停止/恢复；固定运行寿命                |
| Native | `vercel`         | 云沙箱                   | 具有原生会话的命名持久沙箱                     |
| Native | `sprites`        | 云沙箱                   | 持久磁盘与自动休眠/唤醒                        |
| Native | `runloop`        | 云沙箱                   | Devbox 挂起/恢复和空闲保活                     |
| Envd   | `local_envd`     | CLI 和本地 agent         | 共享 Host 设备；关闭适配器结束自身会话         |
| Native | `docker`         | 单机服务                 | Docker Engine 生命周期和 exec；关闭后保留容器  |
| Envd   | `http_envd`      | 网络可达的外部守护进程   | HTTP(S) EIP；仅连接                            |
| Envd   | `websocket_envd` | 主动连接 Host 的守护进程 | 反向 WebSocket EIP；与 Host 集成的 SDK，仅连接 |

Direct Local 共享 Host 账号。Docker 使用原生 Engine 操作；六个云 provider 使用厂商原生传输。它们都无需 Envd。本地和远程 Envd provider 使用 EIP 执行 agent 操作。

单独部署 Envd 时，参见 E2B、Runloop、Vercel、Daytona 和两个 Modal 运行时的[沙箱验证记录](../a13n-envd/egress.md#cloud-platform-validation-record)。它记录被测二进制文件、启动身份、出站网络覆盖和平台限制，不表示这些原生 provider 会启动 Envd 或配置其出站网络策略。

多租户授权和容器分配仍由 Host 负责。一个设备支持多个并发独立会话；会话不是租户分区。Host 共享设备连接，为每个适配器选择固定 cwd，只有 Host 关闭时才关闭共享运行时。

从[内置示例](examples.md)或[本地运行两种远程传输](remote-envd.md)开始。HTTP/WebSocket provider 要求 Host 提供指明设备的 `EnvironmentState`，声明 `supports_managed=False`，不供应或销毁基础设施。WebSocket SDK 接收 Host 提供的已验证连接，绝不打开监听器。

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

Docker 使用 Host 进程的 Engine 连接。无需启动存储、客体守护进程或公开控制端口：

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

原生 provider 覆盖镜像入口，启用 Docker init 支持，并在执行之间保持容器存活。私有工作目录为 `/workspace`。可选绑定挂载将 Docker Engine 所在机器上的已有目录开放到显式容器目标；删除时保留外部数据。不支持命名卷配置。镜像仓库身份验证、凭据 helper、镜像站和代理仍由 Docker 客户端配置。

`close()` 断开本地观测，不停止容器或后台进程。新的托管适配器复用已保存容器；确认不存在时创建替代容器，其私有 `/workspace` 为空。传输失败不能证明目标不存在。Docker 文件路径为原生容器路径，Harness 添加聚合挂载前缀；相对工具路径从 `/workspace` 开始。

Docker 目标配置接受 `pull_policy="if_missing"`（默认）或 `"never"`。`never` 对缺失镜像返回 `environment_image_missing`，绝不联系镜像仓库。使用 `make image-sandbox` 构建镜像，直接 provider 使用见 [Docker 生命周期示例](examples.md#docker)。Service 从模板管理 Docker 环境；参见 [Service 环境](../a13n-service/environments.md)。

## 云 provider

E2B（`e2b`）、Daytona（`daytona`）、Modal（`modal`）、Vercel Sandbox（`vercel`）、Fly.io Sprites（`sprites`）和 Runloop（`runloop`）无需安装 Envd 即可提供云执行。全部支持文件和 shell 命令；E2B 还支持进程观测、stdin、保留 SDK 文本输出和回环端口。Service 提供全部六种环境 provider；其 E2B 账号始终使用 E2B 云（[Service 环境](../a13n-service/environments.md#providers)）。

| Provider       | 后端设置                                                              | 凭据字段                         | 常用目标配置设置                                                              |
| -------------- | --------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------------- |
| E2B            | `domain`、可选 `api_url`                                              | `api_key`                        | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`        |
| Daytona        | `organization_id`、`target`（默认 `us`）                              | `api_key`                        | `snapshot`，决定沙箱资源                                                      |
| Modal          | `workspace`、已有已部署 `app_name`、`environment_name`（默认 `main`） | `token_id`, `token_secret`       | `image`（默认 `python:3.13-slim`）、`cpu`、`memory`（MiB）、`timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                               | `api_key`（Vercel access token） | `runtime`（默认 `python3.13`）、`vcpus`、`timeout_seconds`                    |
| Fly.io Sprites | `organization`                                                        | `api_key`（Sprites token）       | `region`                                                                      |
| Runloop        | `organization`                                                        | `api_key`                        | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                       |

使用拥有所提供凭据的组织、团队或 Modal `workspace`；这些字段描述后端命名空间，不授予权限。凭据绝不属于目标配置或重连状态。

Daytona、Modal、Vercel、Sprites 和 Runloop 目标配置接受 `root`、`python`、`shell`，以及有界请求/文件/输出设置。`python` 指定客体 PATH 上的可执行文件或客体绝对路径，绝不发现 Host 可执行文件。自定义镜像和快照必须包含 Linux、带标准库的 Python 3 和所选 shell。根目录将 `/notes.txt` 等文件路径映射到该客体目录；shell 执行保留原生客体用户权限。默认 Modal 镜像使用 `/usr/local/bin/python3`；Vercel 默认根目录为 `/vercel/sandbox`。这五个 provider 不声明进程句柄、端口、保留输出、交互 stdin、逐命令网络拒绝，或墙钟时间之外的资源限制。不支持的请求在命令执行前失败。

### 重连与生命周期

在 Host 存储中保存最新 `EnvironmentState`，提供给每个新适配器。每个新适配器消费该状态。关闭适配器只释放本地传输。托管分配使用稳定所有权元数据或原生名称；确认丢失后可按冻结配置重建。超时、权限失败和未知响应绝不等于目标不存在。重建改变底层身份，不恢复丢失文件。

| Provider       | 显式停止/恢复                           | 内存             | 过期与保留                                                                                               |
| -------------- | --------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------- |
| E2B            | 原生暂停/恢复保留文件                   | 原生暂停保留内存 | 整个沙箱 TTL 可续期；保活报告观测到的过期时间，不恢复已暂停沙箱。                                        |
| Daytona        | 原生停止/启动，包括已归档沙箱           | 不承诺保留       | 请求禁用自动停止/删除，且无硬 TTL。组织强制硬 TTL 会在就绪检查时被拒绝。                                 |
| Modal          | 文件系统快照后终止；恢复从快照创建沙箱  | 不保留           | 运行中沙箱最多固定 24 小时。保活报告保守的已知期限，拒绝承诺延期。停止快照不过期，成功恢复或销毁后删除。 |
| Vercel Sandbox | 命名原生沙箱停止/恢复，保留文件系统     | 不保留           | 运行会话有有限超时。续期验证返回期限并遵循配置总上限。原生停止快照配置为不过期。                         |
| Fly.io Sprites | 无显式停止 API；空闲时休眠，exec 时唤醒 | 不承诺保留       | 持久文件系统在原生自动休眠后保留。该 provider 不支持 `stop()`，因此不要调度显式停止；销毁是独立操作。    |
| Runloop        | 原生挂起/恢复保留磁盘                   | 不保留           | 空闲策略挂起 Devbox。保活确认新的空闲间隔，不能启动已挂起目标。                                          |

停止绝不会实现为无保护删除。Modal 内部快照属于 provider 状态，不是面向用户的快照资源。删除要求匹配原生所有权标签。以 `allow_create=False` 构建的 Modal 适配器（外部注册）不能使用基于快照的停止/恢复，因为恢复会分配另一个原生沙箱。所有外部注册都要求 provider 状态，绝不隐式分配。Daytona、Modal、Vercel、Sprites 和 Runloop 也拒绝适配器销毁外部管理的目标。

创建被中断时，通过原生名称或所有权元数据核对。已分派但结果未知的命令不重放。Daytona、Modal、Vercel、Sprites 和 Runloop 的每个前台命令都有有限客体期限；传输丢失或取消不能证明命令立即停止。取消关闭本地传输资源；客体命令 runner 限制前台命令并终止其进程组。这不是沙箱级进程隔离：故意分离到新会话的后代需要原生目标生命周期清理。超时输出不完整，不会虚构生产方总量。Modal SDK 用稳定 exec ID 重试原生命令，用稳定快照请求 ID 重试文件系统快照。

### E2B 运行时

E2B 通过原生异步 SDK 直接执行命令。有界 Python helper 只实现文件和端口检查。默认 `base` 模板无需安装 `a13n-envd`、上传可执行文件或构建自定义模板。自定义模板需要 Linux、Python 3.11+、Bash，以及配置的账号/根目录；git-ignore 查询还需要 Git。

```python
import os

from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(E2B,) = select_builtin_environment_providers(("e2b",))
environment = await E2B.create(
    {"template": "base", "timeout_seconds": 300},
    configuration={"domain": "e2b.dev"},
    credential={"api_key": os.environ["E2B_API_KEY"]},
    environment_id="environment-example",
    state=None,
)
```

照常将该环境传给 Harness。在 Host 上持久保存 `environment.dump_state()`，重新进入时提供给新适配器。`close()` 保留沙箱和用户文件，断开自身输出观测，不终止命令。`stop()`（暂停）、`prepare()`（恢复）和 `destroy()`（终止）都使用新适配器。保活报告实际过期时间，绝不恢复暂停沙箱。库不读取 `.env`；Host 将 domain 作为 provider 配置、`api_key` 作为凭据传入。

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

隔离边界是 E2B 沙箱。文件根映射不限制获准 shell 命令。不支持逐命令 CPU、内存和进程数限制。逐命令网络拒绝要求整个沙箱设置 `allow_internet_access=False`，不能追加到已启用互联网的沙箱。端口检查只支持回环 TCP。追加和补丁不保证并发 compare-and-swap。

在 Host 进程环境中设置 `E2B_API_KEY` 后运行示例：

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

示例在 `finally` 中显式销毁沙箱。实时集成测试需主动启用，也会销毁目标：

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### Daytona

默认文件根目录：`/home/daytona`。标准沙箱提供 Python；命令选择客体 PATH 上的 `python3`。自定义快照必须提供所选根目录和可执行文件。停止要求确认已禁用自动删除；删除等待最终销毁，不只接受初始确认。参见 [Daytona 沙箱示例](https://www.daytona.io/docs/en/guides/openai/openai-agents-sdk-with-sandboxes/)和[删除语义](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)。

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
environment = await DAYTONA.create(
    {},
    configuration={"organization_id": "your-organization"},
    # Read this value from your Host's private credential storage.
    credential={"api_key": api_key},
    environment_id="env-workspace",
    state=saved_state,
)
try:
    await environment.prepare()
finally:
    try:
        saved_state = environment.dump_state()
    finally:
        await environment.close()
```

`create()` 先验证目标配置，再验证账号配置和凭据规则，随后才调用 provider 运行时工厂。工厂前的全部步骤无副作用。你传入的运行时仍由你关闭；`create()` 获取的运行时随适配器关闭。

### 云端验证

确定性测试覆盖目录构建、原生 HTTP 响应、Sprites WebSocket 帧，以及 Modal 真实异步 SDK 与本地 gRPC fixture 的交互，执行真实文件和 shell helper。云测试独立且需主动启用：

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-harness/tests/providers_environment/test_cloud_live.py
```

通过私有测试环境提供 `A13N_TEST_DAYTONA_BACKEND_JSON` 和 `A13N_TEST_DAYTONA_CREDENTIAL_JSON`；其他四个 provider 使用对应大写前缀。可选 `A13N_TEST_<PROVIDER>_RECIPE_JSON` 覆盖目标配置。fixture 分配计费目标，并在清理中尝试删除，包括失败后。绝不能提交凭据 JSON。未主动启用或缺少凭据时跳过，不代表完成云端验证。

原生 API 参考：[E2B](https://e2b.dev/docs)、[Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)、[Modal 快照](https://modal.com/docs/guide/sandbox-snapshots)、[Vercel Sandbox SDK](https://github.com/vercel/sandbox)、[Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/) 和 [Runloop 异步 Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html)。
