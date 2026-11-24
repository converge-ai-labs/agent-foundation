---
title: 云端环境提供方配置与限制
sidebarTitle: 云端提供方
description: 配置 E2B、Daytona、Modal、Vercel、Sprites、Runloop 与 boxd 云沙箱，选择原生或 Envd 执行路径并构建自定义桌面镜像。
---

请先在[环境总览](index.md#choose-a-backend)中选择云端后端，结合[环境配置](configuration.md)管理输入归属，并按[环境生命周期](lifecycle.md)持久化运行状态。

## 云端提供方概览

Daytona 与 Modal 统一通过 Envd 执行；E2B、Vercel 与 Sprites 同时支持原生文件/Shell 路径与显式 Envd 路径；Runloop 与 boxd 仅提供原生文件与 Shell。请将 API 密钥保存在凭据存储中，切勿写入配方（Recipe）或 `EnvironmentState`：

| 提供方         | 后端连接配置                                                                   | 凭据字段                   | 常用配方参数                                                                                                  |
| -------------- | ------------------------------------------------------------------------------ | -------------------------- | ------------------------------------------------------------------------------------------------------------- |
| E2B            | `domain`、可选 `api_url`                                                       | `api_key`                  | `template`、`execution`、`preparation`、`desktop`、`user`、`root`、`allow_internet_access`、`timeout_seconds` |
| Daytona        | `organization_id`、`target`（默认 `us`）                                       | `api_key`                  | `snapshot`（`"auto"` 或快照名）、`desktop`、`desktop_assistance`                                              |
| Modal          | 可选 `workspace`、`app_name`（`a13n-sandboxes`）、`environment_name`（`main`） | `token_id`、`token_secret` | `image`（`"auto"` 或镜像 ID）、`desktop`、`desktop_assistance`、`cpu`、`memory`（MiB）、`timeout_seconds`     |
| Vercel Sandbox | `team_id`、`project_id`                                                        | `api_key`（访问令牌）      | `execution`、`snapshot`（`"auto"` 或 ID）、`desktop`、`runtime`、`vcpus`、`timeout_seconds`                   |
| Fly.io Sprites | `organization`                                                                 | `api_key`（Sprites 令牌）  | `region`、`execution`、`desktop`                                                                              |
| Runloop        | `organization`                                                                 | `api_key`                  | `blueprint_id`、`resource_size`、`idle_timeout_seconds`                                                       |
| boxd           | `region`（`fra` 或 `nyc`）、可选 `organization`                                | `api_key`                  | `size`（`1x4`–`8x32`）、`from_snapshot`、`auto_suspend_timeout_seconds`、`auto_hibernate_timeout_seconds`     |

### 重连与生命周期

通过管理平面创建或启动实例后，先持久化返回的 `EnvironmentState`，再由单次运行构造固定目标连接器附着使用，结束后通过管理平面停止或销毁：

| 提供方         | 停止 / 恢复机制                                    | 保留内存 | 过期与保活说明                                                                 |
| -------------- | -------------------------------------------------- | -------- | ------------------------------------------------------------------------------ |
| E2B            | 原生暂停/恢复并保留磁盘文件                        | 是       | 整沙箱可续期 TTL（`timeout_seconds`，默认 `3600`）                             |
| Daytona        | 原生停止/启动（含归档沙箱恢复）                    | 不保证   | 需关闭自动删除且不能启用组织级强制硬 TTL                                       |
| Modal          | 停止时创建文件系统快照；`start()` 从快照拉起新沙箱 | 否       | 单次运行最长 24 小时；停止快照不过期，成功恢复或销毁后自动清理                 |
| Vercel Sandbox | 具名原生停止/恢复并保留文件系统                    | 否       | 会话超时可在上限内续期；原生停止快照不过期                                     |
| Fly.io Sprites | 无显式 `stop()`；空闲自动休眠并在执行时唤醒        | 不保证   | 持久化磁盘跨休眠保留；不再使用时显式销毁                                       |
| Runloop        | 原生挂起/恢复并保留磁盘                            | 否       | 按空闲策略挂起 Devbox；保活用于刷新活跃空闲窗口                                |
| boxd           | 原生休眠（Hibernate）/唤醒，保留磁盘与内存         | 是       | 无硬性过期；配置 `auto_hibernate_timeout_seconds` 后休眠直至显式调用 `start()` |

### E2B 运行时

原生文件与 Shell 可直接使用 `template: "base"`（默认目录 `/home/user`、`/usr/bin/python3`），也可运行 `cd examples/environment-provider && uv run python -m a13n_environment_example.e2b` 体验完整创建与清理流程：

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
        E2B.execution_environment(recipe),
        credential={"api_key": os.environ["E2B_API_KEY"]},
        environment_id="env-example",
        state=state,
    )
result = await executable.run("Inspect the sandbox", environment=PreparedSource(connector))
```

原生 E2B 的输出观测预算独立于沙箱 TTL（`max_active_observations=128`、`max_observation_bytes=1 MiB`、`max_retained_output_bytes=128 MiB`、`request_timeout_seconds=30`）。如需无头安装 Envd，请选用 glibc 2.39+ 模板（Debian 13 / Ubuntu 24.04+），并配置 `{"template": "your-base", "execution": "envd_http", "preparation": "headless", "timeout_seconds": 1200}`（`>= 960` 秒）。

### E2B 桌面环境准备

在 Service 中启用**自动桌面**（`{"template": "auto", "execution": "envd_http", "desktop": true, "desktop_assistance": true}`）即可首次自动构建并在后续复用私有桌面模板；也可使用 Linux `amd64` Envd 二进制自行构建模板：

```bash
cd examples/environment-provider && uv sync --locked
uv run python desktop_template.py my-envd-desktop --binary /absolute/path/to/linux-amd64/a13n-envd
```

文件、Shell 与[桌面操控](../a13n-envd/computer-use.md)统一通过 `/workspace` 上的 EIP 执行，人工查看与接管由桌面监管进程实施可吊销授权。

### Daytona

先在 Daytona 控制台设置组织默认区域，配置具备 Sandbox 与 Snapshot 权限的 API 密钥，即可通过 `snapshot: "auto"`（默认 2 vCPU、4 GiB 内存、10 GiB 磁盘）自动准备无头或桌面 Envd 快照。若为仅具运行权限的密钥预构建自定义快照：

```bash
uv run --with daytona==0.225.0 python examples/environment-provider/daytona_desktop_snapshot.py \
  my-envd --region us --binary /absolute/path/to/linux-amd64/a13n-envd
```

智能体操作走 `8888` 端口 `/workspace` 上的 EIP 协议；人工桌面协助走 `6081` 端口的短效签名预览路由。

### Modal

提供 `token_id` 与 `token_secret`，保持 `image` 为空或设为 `"auto"`（无头 `desktop=false`；开启 `desktop=true` 时默认 2 CPU / 4096 MiB 并支持人工接管）。Modal 与 Daytona 的自动构建均支持可选的摘要锁定 `base_image`（要求 glibc >= 2.39 且未占用 UID/GID 1000）。预构建自定义镜像可执行：

```sh
uv run --locked python examples/environment-provider/modal_desktop_image.py \
  --app a13n-sandbox-dev --binary /path/to/linux-amd64/a13n-envd
```

在配方中填入返回的 `"image": "im-..."`、`"envd_management": "preinstalled"` 以及 `"startup_command": ["a13n-sandbox-entrypoint", "a13n-sandbox-desktop", "a13n-envd", "supervise"]`。EIP 运行在 `/workspace`（UID/GID 1000）的 `8888` TLS 隧道上，与 `6081` 桌面查看隧道隔离。

### Vercel Sandbox

原生文件与 Shell 使用 `execution: "native"`（`/vercel/sandbox`）；配置 `{"execution": "envd_http", "snapshot": "auto", "desktop": false, "timeout_seconds": 2700}`（开启 `desktop: true` 支持桌面操控与人工接管）可在 `/workspace`（UID/GID 1001）自动部署 Envd。也可通过 `uv run --locked python examples/environment-provider/vercel_desktop_snapshot.py --binary /path/to/linux-amd64/a13n-envd` 预构建快照并配合 `"envd_management": "preinstalled"` 与后台 `startup_command` 使用。

### Fly.io Sprites

Sprites 原生执行默认根目录为 `/home/sprite`；配置 `execution="envd_http"`（`desktop=false`）可安装无头 Envd，开启 Desktop 则由 Service [自动安装桌面环境](../a13n-service/computer-use.md#automatic-sprites-desktops)。Sprites 空闲时自动休眠并在执行时唤醒，请勿调用显式 `stop()`。

### Runloop

Runloop Devbox 在 `/home/user` 下结合客体机 `python3` 提供原生文件与 Shell，并通过 `idle_timeout_seconds` 管理空闲挂起。

### boxd

每个环境分配一台网络隔离的 [boxd](https://boxd.sh?ref=agent-foundation) 虚拟机，默认目录为 `/home/boxd`（安装 `a13n-harness[boxd]`，通过 `boxd auth keys create <name>` 创建密钥）。`stop()` 会连同内存与进程一起休眠，`start()` 唤醒恢复。

### 嵌入式宿主配置

在自建 Python 宿主中，结合已保存的 `EnvironmentState` 直接使用 `select_builtin_environment_providers` 附着云端实例：

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(DAYTONA,) = select_builtin_environment_providers(("daytona",))
connector = DAYTONA.execution_connector(
    {"snapshot": "my-envd"},
    configuration={"organization_id": "your-organization"},
    credential={"api_key": api_key},
    environment_id="env-workspace",
    state=saved_state,
)
async with await connector.open() as execution:
    assert execution.operations.files is not None
    listing = await execution.operations.files.list("/", max_results=20)
```

### 云端实机验证

日常回归默认运行离线适配器单元测试；如需执行会创建并清理计费实例的云端实机验证，请显式注入对应凭据后运行：

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

上游 SDK 参考：[E2B](https://e2b.dev/docs)、[Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)、[Modal](https://modal.com/docs/guide/sandbox-snapshots)、[Vercel Sandbox](https://github.com/vercel/sandbox)、[Sprites](https://docs.sprites.dev/api/dev-latest/exec/)、[Runloop](https://runloopai.github.io/api-client-python/sdk/async/devbox.html) 与 [boxd](https://docs.boxd.sh/reference/python-sdk?ref=agent-foundation)。
