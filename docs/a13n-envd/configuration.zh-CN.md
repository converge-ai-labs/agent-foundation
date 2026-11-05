---
title: 守护进程配置与传输
sidebarTitle: 配置与传输
description: 将 Envd 连接到 Host，选择执行设置，并采集资源指标。
---

在启动时配置 Envd，选择连接方式、执行账号、文件系统隔离和网络模式。Host 负责部署、凭据和守护进程生命周期。随后，每个 EIP 会话在这些设置内选择工作目录并执行操作。

本页的 JSON 和环境变量用于守护进程。Harness UI YAML 和 `EnvironmentProviderSpec` 配置 Host 侧适配器，包括必需方法及带秘密引用的可选会话出站策略。需要预置账号、shell 和 sudo 的容器时，使用[沙箱镜像](sandbox.md)。

## 配置层级

Envd 按五层配置启动，后面的层优先：

1. 内置默认值。
2. `--config` 选择的守护进程 JSON 文件。
3. `A13N_ENVD_CONFIG_JSON`，使用相同 schema 校验。
4. 标量 `A13N_ENVD_*` 环境变量。
5. 显式 CLI 参数。

对象递归合并，数组和标量替换已有值。每个 JSON 层必须先通过校验，再参与合并。启动者可完全通过环境变量提供嵌套设置：

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

标量设置覆盖身份、执行、发现、元数据和生命周期：`A13N_ENVD_ALLOW_SUDO`、`A13N_ENVD_EXECUTION_UID`、`A13N_ENVD_EXECUTION_GID`、`A13N_ENVD_EGRESS_MODE`、`A13N_ENVD_FULL_CONTROL`、`A13N_ENVD_COMPUTER_USE`、`A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`、`A13N_ENVD_DIRECTORY_DISCOVERY`、`A13N_ENVD_DEVICE_ID`、`A13N_ENVD_NAME`、`A13N_ENVD_DESCRIPTION`、`A13N_ENVD_DEFAULT_WORKING_DIRECTORY`、`A13N_ENVD_IDLE_TIMEOUT_MS` 和 `A13N_ENVD_DISCONNECT_GRACE_MS`。布尔值接受 `true`、`false`、`1` 和 `0`。

## 连接 Service

使用运行 agent 的主体所持有的普通 Service API token，将它保存为私有文件，然后运行：

```sh
a13n-envd connect-service https://control.example.com --host work \
  --workspace-id ws_example --api-token-file /private/path/api-token \
  --default-working-directory /path/to/project
```

首次接入时，如果当前所有者和工作空间下没有这个设备的环境，Service 会创建它。Envd 将环境 ID 和专用设备凭据保存在本地。后续运行 `a13n-envd connect-service work`，不再需要 API token 文件。首次启动可加 `--environment-id env_example`，要求使用已有环境。`--device-id` 会忽略本地身份文件；后续启动继续传相同的值。仍可使用 `--computer-use` 等通用 daemon 参数。

Service 配置保存在 `--state-dir` 下的 `instances/<instance>/services/<name>`，默认根目录为 `~/.a13n-envd`（Windows 使用平台状态目录）。不同账号或工作空间请使用不同名称。禁用、删除或撤销凭据的环境不会自动重建。离线环境仍可显式[挂载到线程](../a13n-service/environments.md#mount-environments-on-a-thread)，连接本身不会挂载环境。

如果登记成功但凭据丢失，envd 会提示 `client_credential_required` 和环境 ID。先读取环境的当前 ETag，再使用普通 API token 和 `If-Match` 调用 `POST /api/v1/client-environments/{id}/rotate-credential?workspace_id=…`。将返回的 `token` 保存到私有文件，再运行 `a13n-envd connect-service work --credential-file /private/path/device-token`。启动过程不会自动轮换。凭据文件应保持私有（Unix 权限 `0600`）；API token 不会写入配置，也不会用于 WebSocket 鉴权。

## 连接 Harness UI

在实际执行工作的计算机上发起出站连接：

```console
a13n-envd connect https://host.example.com --host work
```

1. 打开打印的批准地址，并登录 Host。
2. 批准匹配的验证码。
3. 保持 Envd 运行。后续启动用 `a13n-envd connect work` 复用注册。

Envd 在设备上生成凭据，因此浏览器配对只需验证码。带已保存状态的重连会复用批准。Service 请使用[首次接入](#connect-to-service)。

使用本机可访问的 Host URL。除 `http://127.0.0.1:8765` 等回环 HTTP 外，必须使用 HTTPS。私有 CA 通过 `--ca-file /path/to/ca.pem` 添加，证书验证保持开启。用 `--name` 设置批准页面的显示名，`--default-working-directory` 选择已有原生目录，`--config` 提供守护进程 JSON 设置。

### 保存的实例与 Host

跨重启保留状态目录，并像账号凭据一样保护它。Unix 默认位置为 `~/.a13n-envd`；Windows 为 `%LOCALAPPDATA%/.a13n-envd`，不可用时回退到 `%USERPROFILE%/.a13n-envd`。用 `--state-dir` 或 `A13N_ENVD_STATE_DIR` 覆盖。

实例将设备 ID 保存在 `instances/<instance>/device-id`。各 Host 的配置、凭据和私有运行时位于 `instances/<instance>/hosts/<host>/`。默认实例为 `default`；省略 `--host` 时会从 URL 派生稳定别名。

为每个 Host 运行独立进程。同一台计算机连接两个 Host 时：

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

重启各进程时，使用原来的实例和 Host 别名。每个进程都有独立注册和会话。`connect` 在前台运行；需要自动启动时，在服务管理器中配置相同的账号、状态目录和命令环境。

认证被拒绝时，Envd 停止并保留凭据。检查 Host 地址、注册状态和挂载的状态目录。真正的新安装应使用新实例并显式批准。

## 独立运行的最小配置

启动由 Host 管理的 stdio 守护进程：

1. 创建并保护绝对路径的运行时父目录。
2. 将下方 JSON 保存为 `/absolute/path/to/a13n-envd.json`，把工作目录路径替换为已有目录。
3. 由 Host 通过 stdin/stdout 管道启动 Envd：

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Envd 为当前代次创建不可预测的私有子目录，并持有独占运行时锁。管道承载 EIP 消息帧，诊断信息写入 stderr。

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

默认工作目录作为各会话的初始 cwd。省略时，Envd 记录启动 cwd。目录不存在会导致会话打开失败，而非守护进程启动失败。文件访问遵循执行账号、Sandbox 授权及外层沙箱的边界，不以 cwd 为边界。目录发现可在会话创建前读取有界的一层目录。

用 `--device-id`、JSON `device_id` 或 `A13N_ENVD_DEVICE_ID` 指定稳定设备 ID，或让安装状态目录保留生成的 ID。CLI 元数据选项覆盖 JSON。目录发现通过 JSON 或 `A13N_ENVD_DIRECTORY_DISCOVERY` 设置。Windows 启动路径使用原生格式，EIP 路径使用 `/C:/...` 或 `/UNC/server/share/...`。

## 系统信息与资源监控

配置与数据解释见 [Device 监控与指标](monitoring.md#system-information-and-resource-monitoring)。

## Prometheus 与 OpenTelemetry

配置与数据解释见 [Device 监控与指标](monitoring.md#prometheus-and-opentelemetry)。

### 原生 OTLP 推送

配置与数据解释见 [Device 监控与指标](monitoring.md#native-otlp-push)。

### Prometheus 抓取配置

配置与数据解释见 [Device 监控与指标](monitoring.md#prometheus-scrape-configuration)。

### OpenTelemetry Collector

配置与数据解释见 [Device 监控与指标](monitoring.md#opentelemetry-collector)。

### 指标与新鲜度

配置与数据解释见 [Device 监控与指标](monitoring.md#metrics-and-freshness)。

## 启用命令

启用 Full Control，以执行账号的权限运行原生 shell：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

JSON `"full_control": true` 选择相同模式，标量环境设置优先。Full Control 在 Unix 提供 `/bin/sh`，在 Windows 提供 PowerShell，并继承启动者环境和 `PATH` 顺序。它排除守护进程启动变量（`A13N_ENVD_*`、`EIP_*`）及 OTLP 指标设置。Shell 别名和未导出变量留在启动 shell 中。逐命令覆盖只影响该命令。

Envd `full_control` 启用执行能力，不提供隔离，并且独立于 Harness UI 的本地模式。Full Control 与手动可执行文件根目录/shell 配置二选一，不能组合。显式命令配置示例：

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "trusted_executable_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

关闭 Full Control，且根目录和配置都为空时，Envd 不声明命令方法。根目录和配置选择可信可执行文件；Host 外层边界及[执行隔离](isolation.md)决定访问范围。[受控会话出站网络](egress.md)添加目标限制和凭据注入。

检查设备声明的执行功能，确认信号、限制和可执行位支持。子进程环境由可继承值和显式命令输入组合而成；守护进程控制变量仍属于启动配置。

## 原生身份与 sudo

> [!IMPORTANT]
> 默认允许原生 sudo。禁用 Sandbox 时，经过授权的软件包安装和系统文件修改会作用于外层沙箱的原始文件系统，并跨会话保留。

Linux 上省略执行 UID/GID 会保留启动者身份，包括 root。Root 启动者切换账号前，先预置账号及 home/工作目录权限，再同时设置 `execution.uid`/`execution.gid`、两个环境变量或两个 CLI 选项。显式 `0:0` 选择 root。对于预置的 `sandbox` 账号：

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

命令、文件 RPC、工作目录访问和目录发现使用所选账号的原生权限。文件 RPC 在权限不足时报告错误，不调用 sudo。命令中的 sudo 影响该命令及其后代，不影响后续文件 RPC。凭据加载和 broker 管理仍由守护进程负责。

切换账号会初始化附加组和账号环境默认值。保留启动者身份会保留原生 capability 继承。从 root 切换用户时，执行命令前移除 Envd 为 supervisor 保留的信号 capability。外层平台和受控出站限制仍然适用；非特权启动者不能选择其他账号。

在镜像或部署中安装 sudo 并配置 sudoers。`allow_sudo` 允许原生提权，但仍受该授权及外层 `no_new_privs`/`nosuid` 限制。Envd 不提供免密码规则。

用以下任一启动设置禁用额外提权：

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

等价的 JSON 设置为：

```json
{
  "execution": {"allow_sudo": false}
}
```

Linux 会对 worker 及其后代应用 `no_new_privs`，阻止通过 sudo、setuid 二进制和文件 capabilities 提权。包括 root 在内的已有权限保留。启动优先级仍然适用（`--allow-sudo true` 覆盖环境变量 `false`）；命令不能通过自身环境改变此策略。不支持该实现的平台拒绝 `false`。UID/GID 选择仅限 Linux，其他平台保留原生启动身份。

## 传输通道配置

按 Host 所需的连接方向选择：

| 配置                | 启动设置                                         | 用途                                   |
| ------------------- | ------------------------------------------------ | -------------------------------------- |
| `stdio`             | 父进程提供的进程管道                             | Host 管理的本地守护进程                |
| `http`              | 绑定地址、受保护凭据文件、TLS 或显式可信明文范围 | Host 主动连接设备                      |
| `reverse_websocket` | 出站 URL 和受保护凭据文件                        | 设备主动连接经过身份验证的 Host 监听器 |

将 `A13N_ENVD_TRANSPORT` 设为所选通道。使用本地 HTTP 客户端时：

1. 准备受保护的 token 文件和已有工作目录。
2. 替换下方命令中的对应路径。命令会创建私有运行时父目录并绑定回环 HTTP。
3. 启动 Envd：

```bash
mkdir -p "$HOME/.a13n-envd-runtime"
chmod 700 "$HOME/.a13n-envd-runtime"
export A13N_ENVD_RUNTIME_DIR="$HOME/.a13n-envd-runtime"
export A13N_ENVD_DEVICE_ID=device-http-example
export A13N_ENVD_TRANSPORT=http
export A13N_ENVD_HTTP_BIND=127.0.0.1:8787
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/private/envd-token
export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback
a13n-envd --default-working-directory /absolute/path/to/workspace
```

Python 客户端使用相同凭据和预期设备 ID，连接基础 origin `http://127.0.0.1:8787`。客户端会选择经过认证的 `/eip/control` 和 `/eip/transfer` 路由。

使用 HTTPS 时，取消设置 `A13N_ENVD_HTTP_PLAINTEXT_SCOPE`，设置 `A13N_ENVD_HTTP_TLS_CERT_FILE` 和 `A13N_ENVD_HTTP_TLS_KEY_FILE`，连接前配置客户端信任。Provider 私网路径使用 `provider_private_link`，并要求客户端显式启用。公共端点要求 HTTPS。代理路由和 TLS 见 [Python 传输选项](python-client.md#http-options)。

出站反向 WebSocket 设置 `A13N_ENVD_REVERSE_WS_URL` 和 `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`；用 `A13N_ENVD_REVERSE_WS_CA_FILE` 添加私有信任。WebSocket 监听器由 Host 管理。

将凭据保存在受保护文件中，不放入 argv、URL、描述符、日志或可移植环境状态。凭据授予设备访问权，会话隔离由执行边界提供。

## 生命周期与归属

先初始化设备连接，再为每个资源范围打开会话。一个守护进程代次服务多个会话，各会话记录 cwd 并管理自己的操作。`session.close` 关闭该范围。保活只续期调用者自己的所有权，因此发现流量不会保留已放弃的会话。

消息帧传输断开后，所有者可在宽限期内、同一代次中显式重新附加。附加恢复对已有资源的访问，不重放命令或恢复传输。过期后应打开新会话并丢弃旧引用。

关闭时，Host 关闭自己的连接，只终止由它管理生命周期的守护进程。会话清理回收进程及私有输出/暂存存储，同时保留工作目录文件。报告清理失败时，应视为回收未完成。

## 接纳工作前检查

检查守护进程版本：

```bash
a13n-envd --version
```

1. 匹配 Python 客户端的精确版本。
2. 从守护进程子环境中移除仅供 Host 使用的 `A13N_ENVD_EXECUTABLE`。未知的 `A13N_ENVD_*` 变量和 JSON 字段会使配置失败。
3. 初始化设备，验证协议和身份，再检查所选会话范围的就绪状态。
4. 通过部署自己的启动者和检查测试外层执行边界。Envd 没有 `isolation probe` 或逐命令隔离模式。

## 独立字段参考

在守护进程 JSON 中使用以下字段：

| 根字段                                       | 默认值或含义                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `device_id`                                  | 显式稳定身份，否则使用安装身份                                                                                       |
| `installation_state_directory`               | 未提供显式设备 ID 时用于持久保存身份                                                                                 |
| `name`, `description`                        | 可选显示元数据                                                                                                       |
| `default_working_directory`                  | 未设置时使用启动 cwd                                                                                                 |
| `directory_discovery`                        | `true`                                                                                                               |
| `idle_timeout_ms`                            | 默认会话空闲寿命：`3600000`（一小时）                                                                                |
| `max_lifetime_ms`                            | 可选正数最大会话寿命；省略表示无限                                                                                   |
| `execution.uid`, `execution.gid`             | 可选、成对的 Linux 原生 ID；省略时保留启动者身份，包括 root                                                          |
| `execution.allow_sudo`                       | `true`；原生 sudoers 仍控制授权                                                                                      |
| `sandbox`                                    | `{"mode":"disabled"}`；或包含目录 `grants` 的 `restricted`                                                           |
| `egress`                                     | `{"mode":"inherit"}`；或 `deny` / `controlled`                                                                       |
| `full_control`                               | `false`；启用时自动配置原生 shell 并继承命令环境                                                                     |
| `computer_use`                               | `false`；主动启用 macOS/X11/Windows 截图与输入方法，要求禁用 Sandbox 并继承出站网络；参见[电脑操作](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`；开始任何 EIP 传输前，等待 macOS 权限或 X11/Windows 桌面就绪的正数启动期限                                  |
| `trusted_executable_roots`, `shell_profiles` | 空；除非启用 Full Control，否则命令方法禁用                                                                          |
| `limits`                                     | 设备总限制和逐会话限制                                                                                               |

默认容量为 128 个会话、设备 256 个并发操作、每个会话 128 个并发操作、设备 4 GiB spool、会话 1 GiB spool、每条输出流 256 MiB 及 2 MiB 预览。会话容量应不超过设备总额，预览不超过流容量，spool 为两条流保留足够容量。

### Shell 配置字段

提供 `profile_id`、`display_name`、`native_executable`、`executable_search_roots` 和正数 `max_script_bytes`。可执行文件和根目录使用已存在的原生绝对路径。可选默认值为 `fixed_arguments: []`、`safe_base_environment: {}` 和 `allow_login_mode: false`。

## 受控会话出站网络

在 Linux 上为各会话限制目标并注入 HTTPS 凭据，请按[会话出站网络](egress.md)操作。启动守护进程时配置受控模式，再通过 EIP 提交策略和秘密值。
