---
title: Provider 与运行时配置
sidebarTitle: Provider 与运行时
description: 准备后端并连接执行，将目标管理与运行时清理分开。
---

Provider 配置分为三部分：目标配置描述要使用的目标，账号设置和凭据用于访问后端，运行时持有共享客户端。先[选择后端](index.md#choose-a-backend)，再按下面对应的流程设置。最后一节介绍如何添加自己的 provider。

## 内置 provider

同时选择后端和执行路径。Native 执行使用厂商的文件与命令 API，Envd 执行使用 EIP。资源创建、停止、恢复和删除仍是独立的管理操作。

| 执行路径      | Provider         | 执行能力                                             | 资源管理              |
| ------------- | ---------------- | ---------------------------------------------------- | --------------------- |
| Native        | `direct_local`   | 本地 Host 上的 Files & Shell                         | Host 管理的已有目录   |
| Native / Envd | `docker`         | Native Files & Shell；显式选择 HTTP EIP 使用通用执行 | Docker provider       |
| Native / Envd | `e2b`            | Native Files & Shell；显式选择 HTTP EIP              | E2B provider          |
| Envd          | `local_envd`     | 通过 stdio 连接本地守护进程                          | Host 管理的守护进程   |
| Envd          | `daytona`        | 通过 EIP 连接兼容的沙箱守护进程                      | Daytona provider      |
| Envd          | `modal`          | 通过 EIP 连接兼容的沙箱守护进程                      | Modal provider        |
| Native / Envd | `vercel`         | Native Files & Shell；显式选择 HTTP EIP              | Vercel provider       |
| Native / Envd | `sprites`        | Native Files & Shell；私有隧道 EIP                   | Sprites provider      |
| Native        | `runloop`        | 厂商 Files & Shell；尚未接入 EIP                     | Runloop provider      |
| Envd          | `http_envd`      | HTTP(S) EIP                                          | 仅连接                |
| Envd          | `websocket_envd` | 反向 WebSocket EIP                                   | 仅连接；Host 管理入口 |

Direct Local 还支持可选的允许列表回环 Ports。Envd 能力取决于守护进程协商结果与 Host 策略。部署 Sandbox 或受控出站网络前，检查[沙箱验证记录](../a13n-envd/egress.md#cloud-platform-validation-record)中的前置条件与历史覆盖范围。

本地执行可从[内置示例](examples.md)开始。已有远程机器可[试用两种远程传输](remote-envd.md)：HTTP/WebSocket 要求 Host 提供指明 Device 的 `EnvironmentState`，基础设施仍由运维人员管理。Host 提供经过身份验证的 WebSocket 入口。

在使用者存活期间保持共享 Device 运行时打开，在 Host 关闭时清理。每个 Envd 执行打开固定 cwd 的独立 Session。Session 拥有资源，租户授权与容器分配仍由 Host 负责。

## Local Envd 运行时

安装兼容的 `a13n-envd` 二进制文件，再构建 Host 拥有的运行时：

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

解析器依次检查显式路径、`A13N_ENVD_EXECUTABLE`，以及 `PATH` 中平台对应的 `a13n-envd` 可执行文件。在 Host 进程中设置环境变量；库不读取 `.env`、安装二进制文件或回退到 Direct Local。

将 `runtime` 传给 Local Envd connector。首次获取时启动一个共享 Device，并检查守护进程与客户端的精确兼容性。每个打开的执行获得独立 Session。使用后关闭执行，在 Host 关闭时调用 `await runtime.close()`。

在获取 Device 前选择启动 Sandbox 和出站网络设置。Envd 在 Host 账号、容器或虚拟机提供的外层边界内实施这些设置；构建运行时不会探测原生隔离能力。按 [Local Envd 示例](examples.md#local-envd)操作，或用 `make local-envd-test` 运行真实 provider 路径。

## Docker 运行时

准备可访问的 Docker Engine 和所需镜像。Native 执行使用 Docker exec；`execution="envd_http"` 还需要兼容的镜像内 Envd 守护进程及可达 HTTP 路由。

嵌入方 Host 可以提供自己的 Engine 客户端：

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

使用者运行期间保持借用的 Engine 打开，在 Host 关闭时清理。镜像仓库认证、凭据 helper、镜像站和代理使用 Docker 客户端配置。

Native provider 覆盖镜像入口，启用 Docker init，并在执行之间保持容器存活。相对工具路径从 `/workspace` 开始；绝对路径指向容器，Harness 另加聚合挂载前缀。绑定挂载将 Engine 所在机器的已有目录开放到显式容器目标。删除容器保留这些外部数据；不支持命名卷配置。

选择 `pull_policy="if_missing"` 使用默认行为，或为预加载镜像选择 `"never"`。使用 `never` 时，缺失镜像返回 `environment_image_missing`，不访问镜像仓库。通过 `make image-sandbox` 构建镜像，再按 [Docker 生命周期示例](examples.md#docker)或 [Service 环境](../a13n-service/environments.md)操作。

关闭执行会释放本地观测，保留容器和后台进程。重新进入使用已保存容器；确认不存在时失败，由管理方显式决定后续操作。传输失败本身不能证明目标不存在。

Native Docker 提供 Files & Shell、进程控制与输出观测。端口探测/等待和 Computer 操作需要 `envd_http`。请显式准备兼容目标；修改已有 Native 目标配置不会安装守护进程。使用 HTTP EIP 时，关闭 Session 保留桌面，停止/启动则保留文件并重建图形状态。独立路由见 [Docker 配置](configuration.md#docker)，启动与权限见[托管桌面设置](../a13n-envd/sandbox.md#managed-docker-desktop-in-service)。

## 云 provider

创建计费目标前，准备所选账号与目标配置。Daytona 和 Modal 使用 Envd。E2B、Vercel 和 Sprites 提供 Native Files & Shell 及显式 Envd 路径。Runloop 提供 Native Files & Shell，尚未集成 Envd、Computer 和人工桌面访问。

| Provider       | 后端设置                                                                                 | 凭据字段                         | 常用目标配置设置                                                                                               |
| -------------- | ---------------------------------------------------------------------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| E2B            | `domain`、可选 `api_url`                                                                 | `api_key`                        | `template`, `user`, `root`, `allow_internet_access`, `timeout_seconds`                                         |
| Daytona        | `organization_id`、`target`（默认 `us`）                                                 | `api_key`                        | `snapshot`，决定沙箱资源                                                                                       |
| Modal          | 可选 `workspace`、`app_name`（默认 `a13n-sandboxes`）、`environment_name`（默认 `main`） | `token_id`, `token_secret`       | Service 自动桌面 `image="auto"` 或兼容 Envd 的自定义镜像、`desktop`、`cpu`、`memory`（MiB）、`timeout_seconds` |
| Vercel Sandbox | `team_id`, `project_id`                                                                  | `api_key`（Vercel access token） | `execution`, `snapshot`, `desktop`, `runtime`, `vcpus`, `timeout_seconds`                                      |
| Fly.io Sprites | `organization`                                                                           | `api_key`（Sprites token）       | `region`                                                                                                       |
| Runloop        | `organization`                                                                           | `api_key`                        | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                                                        |

将凭据保存在目标配置与重连状态之外。组织/团队字段描述后端命名空间，不授予权限。Modal 凭据选择工作区，可选 `workspace` 字段只是标签。Service 的 E2B 连接通过域名选择 E2B 云或兼容后端，见 [Service provider](../a13n-service/environments.md#providers)。

管理端准备目标，并为 Envd 路径准备守护进程与授权连接。EIP 提供执行语义，Sandbox 和出站网络设置实施所选执行策略。已有 Native 目标采用 Envd 时需显式迁移。

### 重连与生命周期

对每个托管目标使用以下顺序：

1. 通过管理端创建或显式启动目标。
2. 向执行提供目标前，保存最新返回的 `EnvironmentState`。
3. 构建固定目标 connector，并在使用后关闭每个执行。
4. 按后端支持的生命周期，通过管理端停止、续期或销毁目标。

| Provider       | 显式停止/恢复                           | 内存             | 过期与保留                                                                                               |
| -------------- | --------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------- |
| E2B            | 原生暂停/恢复保留文件                   | 原生暂停保留内存 | 整个沙箱 TTL 可续期；保活报告观测到的过期时间，不恢复已暂停沙箱。                                        |
| Daytona        | 原生停止/启动，包括已归档沙箱           | 不承诺保留       | 请求禁用自动停止/删除，且无硬 TTL。组织强制硬 TTL 会在就绪检查时被拒绝。                                 |
| Modal          | 文件系统快照后终止；恢复从快照创建沙箱  | 不保留           | 运行中沙箱最多固定 24 小时。保活报告保守的已知期限，拒绝承诺延期。停止快照不过期，成功恢复或销毁后删除。 |
| Vercel Sandbox | 命名原生沙箱停止/恢复，保留文件系统     | 不保留           | 运行会话有有限超时。续期验证返回期限并遵循配置总上限。原生停止快照配置为不过期。                         |
| Fly.io Sprites | 无显式停止 API；空闲时休眠，exec 时唤醒 | 不承诺保留       | 持久文件系统在原生自动休眠后保留。该 provider 不支持 `stop()`，因此不要调度显式停止；销毁是独立操作。    |
| Runloop        | 原生挂起/恢复保留磁盘                   | 不保留           | 空闲策略挂起 Devbox。保活确认新的空闲间隔，不能启动已挂起目标。                                          |

Modal 的 `start()` 恢复保存的文件系统快照，并返回新引用。`open()` 不执行恢复或快照清理。删除快照需要匹配原生所有权标签。外部注册的状态提供连接信息，不授予销毁权限。

创建中断后，核对原生名称或所有权元数据。超时、权限错误和未知响应表示结果未确定，不等于目标不存在。不能隐式重建丢失目标或重放结果不确定的命令。Envd 共用期限、取消、输出和 Session 清理语义；停止与销毁由厂商管理端负责。

### E2B 运行时

第一个 Native 沙箱可使用默认 `base` 模板，无需安装 Envd 或自定义构建。自定义模板需要 Linux、Python 3.11+、Bash，以及配置的账号/根目录；Git-ignore 查询还需要 Git。

要将创建流程接入 Harness，准备 `E2B_API_KEY`、应用状态存储与 `environment_key`、`executable`，以及 [`PreparedSource` 辅助类](../a13n-harness/environments.md#supply-a-source)。此片段创建计费目标并发布状态；之后必须由管理代码停止或销毁它：

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

打开执行以只读查询连接运行中的目标，不续期或恢复，并拒绝配置为自动恢复的目标。关闭执行保留沙箱、文件和命令，同时断开执行观测。由 Host 提供凭据；库不读取 `.env`。

新执行可发现同一沙箱中仍在运行的原生命令。请检查返回结果：沙箱身份不能证明进程仍存活，缺失命令不会自动重启。SDK 原生发现不分页；返回投影有界，上游清单无界。

分别调整输出和沙箱寿命。输出是 SDK 解码后的文本，不是无损字节：

| 设置                        | 默认值  | 范围                                                    |
| --------------------------- | ------- | ------------------------------------------------------- |
| `max_active_observations`   | 128     | 活跃原生附加，包括待启动/连接；完成或断开的观测释放名额 |
| `max_observation_bytes`     | 1 MiB   | 一次观测累计 stdout/stderr 总量，包含随后被驱逐的输出   |
| `max_retained_output_bytes` | 128 MiB | 整个适配器的保留输出；优先驱逐最旧已关闭日志            |
| `timeout_seconds`           | 3600 秒 | 整个沙箱 TTL，不是命令期限                              |
| `request_timeout_seconds`   | 30 秒   | 原生请求超时，不是工具等待时间                          |

长时间运行的命令应将持久应用日志写入文件。观测限制如下：

- 发现不分配输出缓冲或活跃名额。引用达到 100,000 时，先释放引用再接纳更多；已完成句柄不会被悄悄作废。
- 内存压力可驱逐已关闭输出，同时保留引用、已知状态和偏移量。读取报告 `observation_evicted`、部分覆盖及可用范围。
- 达到命令累计预算或耗尽活跃总缓冲时，断开观测但不终止命令。反复等待和状态查询不会重置预算。
- 达到上限前重连，会向同一日志追加内容并标记部分覆盖。驱逐不补充额度；新执行创建新观测。

使用 E2B 的登录 Bash 和原生 kill。不支持直接 argv、显式非登录模式、移除环境变量、interrupt/terminate、进程树验证及逐命令硬期限。`limits.wall_time_seconds` 和 shell `execution_timeout_seconds` 在启动前被拒绝。原生 stdin 按请求限制大小。请求等待、工具等待和沙箱 TTL 是不同限制；没有 observer 时 TTL 仍有效。

沙箱是隔离边界。文件根映射不限制 shell 命令。CPU、内存和进程数限制不能按命令配置。网络拒绝要求整个沙箱设置 `allow_internet_access=False`，不能追加到已启用互联网的目标。追加和补丁不提供并发 compare-and-swap 保证。

要运行包含清理的完整示例，先安装[示例项目](examples.md#install)，在 Host 环境中设置 `E2B_API_KEY`，再从仓库根目录运行：

```bash
cd examples/environment-provider
uv run python -m a13n_environment_example.e2b
```

预期输出包括文件读取、Provider 名称，以及恢复的原生命令或明确的不可发现说明。示例在 `finally` 中销毁沙箱。从仓库根目录用以下命令启用实时集成测试：

```bash
A13N_TEST_E2B_API_KEY="$E2B_API_KEY" make e2b-provider-test
```

### E2B 桌面准备 {#e2b-desktop-preparation}

在 Console 中选择 E2B 连接并启用**桌面环境**。**自动桌面**在首次创建时准备私有模板，后续复用。凭据需同时允许模板构建和沙箱操作。首次创建耗时更长；官方 E2B `desktop` 模板本身缺少 Envd。

显式目标配置使用 `execution="envd_http"` 和 `desktop=true`。自动桌面启用 `desktop_assistance=true`；自定义模板需要人工查看/控制时请自行启用。Agent Computer 工具和挂载权限单独配置。

自动构建固定基础镜像、Envd 发布版本和启动文件校验和。复用按工作区、连接、后端、凭据版本和构建清单隔离。凭据/清单变化会为新实例准备新模板，已有实例保留原选择。失败构建保留诊断身份，清理前需检查所有权。成功模板保留为可复用账号资源。

要自行维护模板，选择**自定义模板**。准备 `E2B_API_KEY` 和兼容的 Linux amd64 Envd 二进制，从仓库根目录运行[模板构建器](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/desktop_template.py)：

```bash
cd examples/environment-provider
uv sync --locked
uv run python desktop_template.py my-envd-desktop --binary /absolute/path/to/linux-amd64/a13n-envd
```

将生成的模板名称或 ID 填入 Console。构建器包含共享桌面启动器、Noto 中文字体、Chromium 和访问 supervisor。创建时，Service 启动组件、生成每个沙箱独立的私有凭据，并通过 E2B HTTPS 端口路由连接。文件、shell 和 Computer 操作在 `/workspace` 使用 EIP；Native 账号/根目录/helper 设置只用于 Native 执行。打开已有目标仅检查就绪，不修复或重启组件。

Supervisor 实施人工授权与撤销，Service 协调安全暂停/恢复。Envd 负责截图、坐标和输入。Linux X11 支持物理按键，但拒绝 Unicode 文字输入，不回退到 E2B Desktop SDK。见 [Envd Computer use](../a13n-envd/computer-use.md)。

### Daytona

在 Service / Console 中准备自动桌面：

1. 在 Daytona 控制台设置组织默认区域。连接区域不能替代该账号设置。
2. 添加具备 Sandbox 操作及 Snapshot 读写/删除权限的 Key。`organization_id` 是可选约束，默认目标区域为 `us`。
3. 启用 Desktop 并创建环境。仅保存模板不会开始构建。
4. 查看显示的构建阶段。可以离开页面再返回环境实例；确认失败后先修复原因再重试。

首次创建构建包含 Envd 与桌面/访问组件的私有快照，后续复用记录的 ID。自动快照使用 2 CPU、4 GiB 内存和 10 GiB 磁盘。复用按工作区、Provider、后端、凭据版本和清单隔离，并重新核验所有权、身份和内容。提交结果不确定时先核对，再决定是否重新构建。通过校验后清理失败构建；就绪快照在实例停止/删除后保留。

自定义快照（包括无桌面目标或仅具备运行权限的 Key）需准备 Linux amd64 二进制及具有快照权限的私有 `DAYTONA_API_KEY`。从仓库根目录运行：

```bash
uv run --with daytona==0.225.0 python examples/environment-provider/daytona_desktop_snapshot.py my-envd --region us --binary /absolute/path/to/linux-amd64/a13n-envd
```

Files & Shell 使用 `{"snapshot": "my-envd"}`；Computer 和人工访问再添加 `"desktop": true, "desktop_assistance": true`。自定义快照的运行 Key 不需要构建权限。快照启动 Envd 及启用的桌面/访问组件；Service 在创建/启动后检查 EIP 就绪。连接和访问授权不安装或修复组件。

Agent 操作在 `/workspace` 通过私有 8888 端口使用 EIP。Daytona 代理认证与客体 Envd 凭据独立且仅保留在内存中，Host 校验发现的端点。不提供 Native 回退。Computer 工具与挂载权限单独开启。快照包含中文字体，但 X11 接受物理按键，不接受中文或多行文字的直接输入。

人工访问使用 6081 端口的短期签名预览路由。Supervisor 强制实施只读查看、单一控制者、可续期授权和归还时断开连接。仅撤销签名 URL 不能证明已有输入连接已经终止。Service 在交接前等待安全执行边界，并在归还后要求重新观察。

停止/启动保留文件，但重建桌面进程并使旧观察失效。停止要求确认自动删除已禁用，删除等待最终销毁。`test_daytona_desktop_live.py` 覆盖 EIP/桌面/停止启动，`test_daytona_access_live.py` 覆盖查看、撤销、过期和重启隔离。这些计费目标测试与完整 Console/Agent 交接验收分别执行。

### Modal

在 Service / Console 中准备自动桌面：

1. 提供 Modal Token ID 和 Token Secret。凭据选择账号，`workspace` 是可选标签。
2. 启用 Desktop，省略 `image` 或使用 `auto`。空连接设置选择 `main` 环境中的 `a13n-sandboxes` App，首次准备时按需创建 App。
3. 创建环境以开始准备。查看构建阶段和已用时间；离开 Console 不会停止构建。
4. 打开就绪预览，或在确认失败后修复问题再重试。

首次创建从固定版本的公开文件构建，后续复用不可变镜像 ID。默认 2 CPU、4096 MiB、3600 秒并启用人工协助，保留显式覆盖值。构建记录按工作区、Provider、凭据版本和清单隔离。复用检查当前镜像访问权；等待中断后重新等待同一内容寻址构建，不分配临时沙箱。镜像和 App 保留为账号资源。

自定义镜像和无桌面目标需要显式兼容的 `image` 及已有 App。用 `modal token new` 登录，准备 Linux amd64 Envd 二进制，再从仓库根目录运行可选构建器：

```sh
uv run --locked python examples/environment-provider/modal_desktop_image.py \
  --app a13n-sandbox-dev --binary /path/to/linux-amd64/a13n-envd
```

构建器不包含运行时凭据。将生成的镜像 ID 写入目标配置：

```json
{
  "image": "im-yourImageId",
  "desktop": true,
  "desktop_assistance": true,
  "cpu": 2,
  "memory": 4096
}
```

Envd 以 UID/GID 1000 在 `/workspace` 运行。`desktop` 要求创建/恢复时桌面可观察且可输入，Service 的 `desktop_assistance` 还要求访问 supervisor。独立 TLS 隧道分别在 8888 承载 EIP、在 6081 承载人工查看/控制。浏览器不接收 Modal 或 Envd 凭据。Agent Computer 工具与挂载权限单独配置。

进程内客户端缓存最多保留 32 项，空闲 120 秒后过期，所有项最多保留 10 分钟。每次请求仍检查当前所有权、存活状态、桌面 generation/epoch、凭据版本和端点策略。成功后释放独占 SDK 借用；Envd 在 Agent 执行前释放该借用。错误、取消、生命周期变化和关闭会淘汰对应传输，不重放命令。独立 control/worker 进程分别预热缓存。

托管停止先保存文件快照再终止。显式启动恢复文件并启动新的桌面进程，成功就绪后允许清理快照。旧观察和人工授权失效。运行 TTL 不超过已知期限，最多 24 小时；连接既不恢复也不续期。

实时验证需设置 `A13N_TEST_MODAL_DESKTOP=1`、`A13N_TEST_MODAL_IMAGE`、`A13N_TEST_MODAL_BACKEND_JSON` 和 `A13N_TEST_MODAL_CREDENTIAL_JSON`。`test_modal_desktop_live.py` 覆盖 EIP/桌面/恢复，`test_modal_access_live.py` 覆盖认证/撤销/过期。`test_modal_takeover_live.py` 使用脚本模型和预置就绪生命周期记录，检查 Service HTTP 准入、安全暂停、真实 RFB 控制、撤销与后续观察。Console 浏览器界面需要单独验收。这些测试创建计费目标并尝试清理。

### Vercel Sandbox

Native Files & Shell 保持 `execution="native"`。Computer use 需准备运维构建的快照，选择 `execution="envd_http"`、`desktop=true`，需要人工访问时再启用 Service 的 `desktop_assistance=true`。

安全配置 `VERCEL_TOKEN`、`VERCEL_TEAM_ID` 和 `VERCEL_PROJECT_ID`。从仓库根目录使用兼容 Linux amd64 Envd 二进制构建：

```sh
uv run --locked python examples/environment-provider/vercel_desktop_snapshot.py --binary /path/to/linux-amd64/a13n-envd
```

上传较慢时，用兼容的官方 `--archive-url` 及其 `SHA256SUMS` 中已核实的 `--archive-sha256` 替代 `--binary`。沙箱校验下载内容后只提取 Envd。

构建器分配临时计费沙箱，安装桌面依赖与 Chrome，并上传共享启动器。它保存不含运行中守护进程或客体凭据的快照，再删除构建沙箱。使用返回的快照 ID：

```json
{
  "execution": "envd_http",
  "snapshot": "snap_yourSnapshot",
  "desktop": true,
  "desktop_assistance": true,
  "vcpus": 2,
  "timeout_seconds": 1800
}
```

Envd 以 UID/GID 1001 使用 `/workspace`，Native 执行使用 `/vercel/sandbox`。创建与显式恢复启动预装组件，并检查 EIP/Computer 就绪。连接和访问不安装、修复或恢复。8888 端口承载已认证 EIP，6081 承载可撤销查看器。浏览器接收短期 supervisor 凭据，不接收 Vercel 或客体 token。Service 检查所有权、原生身份、当前 session 和端点策略。停止保留文件；恢复重建桌面进程并使旧观察/授权失效。

### Fly.io Sprites

默认根目录使用 `/home/sprite`，helper 使用客体 PATH 中的 `python3`。[Sprites 环境指南](https://docs.sprites.dev/working-with-sprites/)介绍可写 home 和工具。原生休眠保留磁盘，执行会唤醒 Sprite。不要调度显式停止；销毁等待确认不存在。

需要 Computer 和人工控制时，让 Service [在每个新 Sprite 中安装桌面](../a13n-service/environments.md#automatic-sprites-desktops)，再以 `execution="envd_http"` 连接。Native 执行仍可使用。

### Runloop

默认根目录使用 `/home/user`，Python 使用客体 PATH 中的 `python3`。参见 Runloop 的[默认非特权用户](https://docs.runloop.ai/docs/devboxes/configuration/user-parameters)和 [Python 环境](https://docs.runloop.ai/docs/devboxes/overview)。保活采用返回的空闲间隔，清除状态前检查关闭完成。此 provider 用于 Native Files & Shell；历史独立 Envd 探测不能证明 Provider 或 Console 已集成。

### 嵌入式配置

从自己的 Host 连接前，先从管理端取得 `saved_state`，从凭据存储取得 `api_key`。以下异步集成片段选择内置 Daytona 定义并读取有界目录列表：

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

`execution_connector()` 验证输入，不获取客户端。`open()` 获取执行资源。在 Host 关闭时清理借用的运行时；管理和执行客户端拥有独立生命周期。

### 云端验证

先运行确定性适配器测试，检查目录构建、原生 HTTP 响应、Sprites WebSocket 帧和 Modal 异步 SDK 与本地 gRPC fixture 的交互。这些测试执行文件/shell helper，不代表已验证部署中的云账号。

要实时检查 Daytona，安全提供 `A13N_TEST_DAYTONA_BACKEND_JSON` 和 `A13N_TEST_DAYTONA_CREDENTIAL_JSON`，再从仓库根目录运行：

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

Modal、Vercel、Sprites 和 Runloop 使用对应大写前缀。按需用 `A13N_TEST_<PROVIDER>_RECIPE_JSON` 覆盖目标配置。fixture 创建计费目标，即使失败也尝试删除。凭据 JSON 保持私有。未启用或缺少凭据会跳过，不是实时验证通过。

原生 API 参考：[E2B](https://e2b.dev/docs)、[Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)、[Modal 快照](https://modal.com/docs/guide/sandbox-snapshots)、[Vercel Sandbox SDK](https://github.com/vercel/sandbox)、[Sprites exec](https://docs.sprites.dev/api/dev-latest/exec/) 和 [Runloop 异步 Devbox](https://runloopai.github.io/api-client-python/sdk/async/devbox.html)。

## Provider 目录与插件

使用 `ProviderCatalog` 精确允许 Host 支持的定义。同一不可变目录适用于 Model、Web、Connector、Memory 和 Environment 领域。

安装发布入口 `acme` 和环境类型 `acme_sandbox` 的插件后，将它与选定的内置定义一起加载：

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

加载器只导入指定名称的入口，空选择不扫描已安装元数据。它报告分发包、版本和导入来源，并拒绝重复、格式错误、缺失或含糊的名称。目录还拒绝重复的 provider 类型，因此插件不能覆盖内置定义。修改插件代码后重启 Host。

发布自己的插件时，在分发包的 `pyproject.toml` 中添加入口：

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_agent_environment:manifest"
```

在 `acme_agent_environment` 中导出包含已定义的 `ACME_SANDBOX` 的 manifest：

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

按以下顺序构建环境定义：

1. 选择稳定的 `type`、显示名称和可选 HTTPS 设置链接。
2. 分别定义账号配置、凭据和目标配置模型。
3. 提供执行元数据。管理功能使用独立的 Service `EnvironmentManagementDefinition`。
4. 实现纯 `connector_factory`，返回固定目标 `EnvironmentConnector`。接受每次传入的 `session_recovery` 关键字，包括 `None`；Native 实现接受它但不生成 Session 提示。
5. 每次 `open()` 返回新的已就绪 `EnvironmentExecution`，拥有独立 `execution_id` 和操作对象。
6. 验证目标引用而不修改权威状态。创建、启动、替换、续期和销毁交给管理端，放在执行的打开/检查/关闭之外。

继续阅读可运行的 [Provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)和[共享插件契约](../a13n-harness/plugins.md#provider-plugins)。完整的内置管理、状态重新进入与销毁见 [Provider 示例](examples.md)。
