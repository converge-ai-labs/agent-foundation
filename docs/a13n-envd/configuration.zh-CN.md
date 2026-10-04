---
title: 守护进程配置与传输
sidebarTitle: 配置与传输
description: 配置独立 Envd 守护进程及其连接 Host 的方式。
---

这是独立 Envd 配置，不是 Harness UI YAML 或 `EnvironmentProviderSpec`。Host 负责部署、账号选择、外层沙箱、凭据和守护进程生命周期。适配器配置选择工作目录、必需方法，以及可选的会话出站网络策略；该策略中的秘密是引用，不是秘密值。设备启动配置则单独固定执行、Sandbox 和网络模式。[沙箱镜像](sandbox.md)提供可直接使用的账号、shell 和 sudo 默认值，不改变独立守护进程默认值。

## 配置层级

启动优先级从低到高如下：

1. 内置默认值。
2. 由 `--config` 选择的守护进程 JSON。
3. `A13N_ENVD_CONFIG_JSON`，使用与文件相同的严格 schema。
4. 标量 `A13N_ENVD_*` 环境变量。
5. 显式命令行参数。

对象递归合并；数组和标量替换之前的值。即使被后续层覆盖，每个 JSON 层本身也必须有效。沙箱启动者因此可以完全通过环境变量配置 Envd，包括嵌套限制和 shell 配置，无需写配置文件：

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

标量快捷变量包括 `A13N_ENVD_ALLOW_SUDO`、`A13N_ENVD_EXECUTION_UID`、`A13N_ENVD_EXECUTION_GID`、`A13N_ENVD_EGRESS_MODE`、`A13N_ENVD_FULL_CONTROL`、`A13N_ENVD_COMPUTER_USE`、`A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`、`A13N_ENVD_DIRECTORY_DISCOVERY`、`A13N_ENVD_DEVICE_ID`、`A13N_ENVD_NAME`、`A13N_ENVD_DESCRIPTION`、`A13N_ENVD_DEFAULT_WORKING_DIRECTORY`、`A13N_ENVD_IDLE_TIMEOUT_MS` 和 `A13N_ENVD_DISCONNECT_GRACE_MS`。布尔值接受 `true`、`false`、`1` 或 `0`。

设备初始化协商协议并验证设备身份。EIP 会话选择固定 cwd，管理操作、进程、输出和传输；不能更改可信启动身份或 sudo 策略。

## 连接 Harness UI

连接 Harness UI 时，启动一个出站连接：

```console
a13n-envd connect https://host.example.com --host work
```

1. 打开 Envd 打印的批准地址。
2. 登录 Host。
3. 批准匹配的验证码。

保持 Envd 运行。后续启动时，`a13n-envd connect work` 会复用已保存的身份、凭据和已批准连接。凭据在设备上生成，始终无需复制到浏览器。重连无需再次批准。Service 则连接你注册的 HTTP 守护进程；参见[连接 Service](../environments/remote-envd.md#connect-to-the-service)。

本机必须能够访问 Host URL。除 `http://127.0.0.1:8765` 等回环 HTTP 外，必须使用 HTTPS。私有 CA 可添加 `--ca-file /path/to/ca.pem`；始终不能禁用 TLS 验证。`--name` 设置批准页面的显示名，`--default-working-directory` 选择已存在的原生目录。`--config` 提供普通守护进程 JSON，包括资源限制和命令设置。

### 保存的实例与 Host

状态在 Unix 上默认存入 `~/.a13n-envd`，Windows 上存入 `%LOCALAPPDATA%/a13n-envd`（不可用时回退到用户配置目录）。通过 `--state-dir` 或 `A13N_ENVD_STATE_DIR` 覆盖。每个实例在 `instances/<instance>/device-id` 保存身份；命名 Host 的配置、凭据和私有运行时位于 `instances/<instance>/hosts/<host>/`。默认实例名为 `default`。未指定 `--host` 时，从 URL 派生稳定名称。请跨重启保留状态，并像账号凭据一样保护它。

每个运行中的 Envd 进程连接**一个 Host**。同一台物理计算机需要使用两个 Host 时，运行两个独立进程：

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

每个进程有自己的注册和会话。重启时使用同一个 `--instance` 和 Host 别名。`connect` 保持前台运行，不安装系统服务，也不修改操作系统启动设置。使用服务管理器时，保留启动账号、状态目录和预期命令环境。

身份验证被拒绝时，守护进程停止，不会替换凭据或请求新的信任关系。检查注册是否被撤销或遗忘、是否连接正确的 Host，以及是否挂载正确的状态目录。不要反复删除凭据来掩盖授权失败。全新安装需要新实例和显式批准。

## 独立运行的最小配置

默认传输通道为 stdio。提供绝对路径的私有运行时目录：

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Host 创建并保护运行时父目录。Envd 创建不可预测的代次私有子目录，并取得独占运行时锁。stdin 和 stdout 专用于 EIP 消息帧；诊断信息使用 stderr。

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

会话只能在已存在的目录中打开；默认目录不存在时，会话打开失败，守护进程启动不受影响。省略 `default_working_directory` 时，守护进程记录启动时的 cwd。工作目录**不是访问根目录**：文件路径可以指向执行账号、Sandbox 授权和外层沙箱允许的整个文件系统。目录发现是有界的单层读取，在任何会话创建前即可使用。

显式设备 ID 可通过 `--device-id`、JSON `device_id` 或 `A13N_ENVD_DEVICE_ID` 提供。没有显式 ID 时，安装状态目录跨重启保留生成的身份。`--default-working-directory`、`--name` 和 `--description` 覆盖 JSON 元数据。通过 JSON 或 `A13N_ENVD_DIRECTORY_DISCOVERY` 设置目录发现。Windows EIP 路径使用 `/C:/...` 和 `/UNC/server/share/...`；守护进程启动路径使用操作系统原生格式。

## 启用命令

需要以配置的执行账号权限运行原生 shell 时，使用：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

也可在守护进程 JSON 中设置 `"full_control": true`。`A13N_ENVD_FULL_CONTROL` 覆盖 JSON 值；`false` 或 `0` 将其禁用。Full Control 自动创建默认原生 shell 配置：Unix 上为 `/bin/sh`，Windows 上为 PowerShell。命令继承启动进程的环境和原始 `PATH` 顺序，排除守护进程启动变量（`A13N_ENVD_*` 和 `EIP_*`）。命令级环境修改不会影响守护进程或后续命令。shell 别名和未导出的变量不会被继承。

Envd Full Control（`full_control`）不是沙箱。它独立于 Harness UI 本地的 Full Control 或 Sandbox 模式。不要与手动可执行文件根目录或 shell 配置组合使用。需要显式配置命令访问时，提供可信可执行文件搜索根目录，并按需提供固定 shell 配置：

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

Full Control 禁用，且可执行文件根目录和 shell 配置都为空时，不声明任何命令方法。可执行文件根目录和配置是可信启动配置，不是会话沙箱。Host 提供外层安全边界；可选的[受控会话出站网络](egress.md)提供逐会话目标限制和凭据注入。参见[执行边界与故障排查](isolation.md)。

子进程环境由守护进程支持继承的值和显式命令输入构成。守护进程控制变量不是命令配置。请检查声明的执行功能，不要假定平台支持信号、限制或可执行位。

## 原生身份与 sudo

> [!IMPORTANT]
> **默认允许原生 sudo。** 禁用 Sandbox 时，Envd 保持外层沙箱的原始系统树可写。通过授权 sudo 安装软件包或修改系统文件会改动该树，并跨会话保留；不会为工作负载根文件系统引入临时副本。

Linux 上，**省略执行 UID/GID 会保留启动进程身份，包括 root**。Envd 不会假定 `1000:1000`、搜索可能的用户、创建账号，也不会仅因启动者为 root 就强制要求身份配置。要选择其他预置账号，成对设置 `execution.uid`/`execution.gid`、`A13N_ENVD_EXECUTION_UID`/`A13N_ENVD_EXECUTION_GID`，或 `--execution-uid`/`--execution-gid`。Root（`0:0`）也是有效的显式身份。provider 或部署负责账号预置、home 和工作目录权限。例如，root 启动者可以选择其预置的 sandbox 账号：

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

命令、会话文件 RPC（包括读取、写入和传输）、工作目录访问和设备目录发现都使用该执行身份及其原生权限。文件 RPC 绝不会隐式 sudo；访问不足会返回权限错误。显式 sudo 仅影响调用它的命令及其后代，不影响后续文件 RPC。守护进程凭据加载和 broker 管理独立于执行身份。

切换账号会初始化原生附加组和账号环境默认值。保留启动者身份时，root 与非 root 用户都保留原生 capability 继承；Envd 不会无条件清空命令 capabilities。只有从 root 切换用户时，Envd 为 supervisor 保留的信号 capability 会在执行命令前移除。外层平台及显式启用的受控出站网络边界仍然适用。非特权启动者不能选择其他身份。

`allow_sudo` 允许原生提权，并不授予授权。通过镜像或启动者安装 sudo 并配置 sudoers。Envd 不添加免密码 sudo 规则，不绕过身份验证，也不覆盖外层 `no_new_privs` 设置或 `nosuid` 挂载。

可用以下任一方式禁用提权：

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

或使用 JSON 文件：

```json
{
  "execution": {"allow_sudo": false}
}
```

遵循普通优先级：例如 `--allow-sudo true` 会覆盖值为 `false` 的环境变量。禁用时，在 Linux worker 及其后代上设置 `no_new_privs`，因此直接 sudo、setuid 二进制文件和文件 capabilities 都不能取得额外权限。这不会降低已经是 root 的执行身份，也不会撤销其已有权限。命令自身环境不能重新开启提权。不支持此实现的平台会拒绝 `false`，而非忽略它。UID/GID 配置仅限 Linux；其他平台保留原生启动身份。

## 传输通道配置

| 配置                | 启动设置                                         | 用途                                   |
| ------------------- | ------------------------------------------------ | -------------------------------------- |
| `stdio`             | 父进程提供的进程管道                             | Host 管理的本地守护进程                |
| `http`              | 绑定地址、受保护凭据文件、TLS 或显式可信明文范围 | Host 主动连接设备                      |
| `reverse_websocket` | 出站 URL 和受保护凭据文件                        | 设备主动连接经过身份验证的 Host 监听器 |

通过 `A13N_ENVD_TRANSPORT` 选择配置。

HTTP 需要 `A13N_ENVD_HTTP_BIND`、`A13N_ENVD_HTTP_CREDENTIAL_FILE`，以及成对的 TLS 证书/密钥文件或 `A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback|provider_private_link`。它提供经过身份验证的 `/eip/control` 和 `/eip/transfer` 路由。会话必须显式选择；TCP 连接和 HTTP 连接池都不拥有会话。

反向 WebSocket 需要 `A13N_ENVD_REVERSE_WS_URL` 和 `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`。`A13N_ENVD_REVERSE_WS_CA_FILE` 为 `wss` 添加部署信任。守护进程不提供入站 WebSocket 监听器。

凭据应存入受保护文件，不能放入 argv、端点 URL、描述符、日志或可移植环境状态。凭据授权设备访问，不会将会话彼此隔离。

## 生命周期与归属

一个守护进程代次服务于一个设备和多个独立会话。初始化传输通道不会打开会话。`session.open` 记录工作目录并启动一个资源范围；`session.close` 只关闭该范围。会话内保活只续期自己的所有者。设备发现不会让已放弃会话持续存活。

消息帧传输通道丢失后，会话分离并进入有限的断线宽限期。已有所有者可以在同一代次显式重新附加到同一会话。附加绝不会重放命令或恢复传输。过期会话需要新资源范围，旧资源引用仍然无效。

Host 关闭时会关闭自己的设备连接，且只有拥有守护进程生命周期时才会终止该进程。会话关闭不会删除工作目录中的文件。原生进程和私有输出/暂存存储在有界清理中回收；失败会被报告，不会被视为已成功回收。

## 接纳工作前检查

```bash
a13n-envd --version
```

匹配守护进程和 Python 客户端的精确版本。`A13N_ENVD_EXECUTABLE` 用于 Python Host 选择可执行文件，应从守护进程子环境中移除。未知的 `A13N_ENVD_*` 变量和 JSON 字段会被拒绝。

设备初始化验证身份和协议。会话就绪检查验证所选范围。不存在 `isolation probe` 或逐命令隔离模式：通过部署自身的启动者和检查来测试 Host 外层边界。

## 独立字段参考

| 根字段                                       | 默认值或含义                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `device_id`                                  | 显式稳定身份，否则使用安装身份                                                                                       |
| `installation_state_directory`               | 未提供显式设备 ID 时用于持久保存身份                                                                                 |
| `name`, `description`                        | 可选显示元数据                                                                                                       |
| `default_working_directory`                  | 未设置时使用启动 cwd                                                                                                 |
| `directory_discovery`                        | `true`                                                                                                               |
| `idle_timeout_ms`                            | 会话空闲寿命                                                                                                         |
| `disconnect_grace_ms`                        | 已分离会话重新附加的宽限期                                                                                           |
| `execution.uid`, `execution.gid`             | 可选、成对的 Linux 原生 ID；省略时保留启动者身份，包括 root                                                          |
| `execution.allow_sudo`                       | `true`；原生 sudoers 仍控制授权                                                                                      |
| `sandbox`                                    | `{"mode":"disabled"}`；或包含目录 `grants` 的 `restricted`                                                           |
| `egress`                                     | `{"mode":"inherit"}`；或 `deny` / `controlled`                                                                       |
| `full_control`                               | `false`；启用时自动配置原生 shell 并继承命令环境                                                                     |
| `computer_use`                               | `false`；主动启用 macOS/X11/Windows 截图与输入方法，要求禁用 Sandbox 并继承出站网络；参见[电脑操作](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`；开始任何 EIP 传输前，等待 macOS 权限或 X11/Windows 桌面就绪的正数启动期限                                  |
| `trusted_executable_roots`, `shell_profiles` | 空；除非启用 Full Control，否则命令方法禁用                                                                          |
| `limits`                                     | 设备总限制和逐会话限制                                                                                               |

主要默认限制包括 128 个会话、设备 256 个并发操作、每个会话 128 个并发操作、设备 4 GiB spool 容量、会话 1 GiB spool 容量、每条流 256 MiB 输出和 2 MiB 预览。配置的会话容量不能超过设备总额。输出预览不能超过流容量；spool 必须为两条流预留空间。

### Shell 配置字段

必填字段为 `profile_id`、`display_name`、`native_executable`、`executable_search_roots` 和正数 `max_script_bytes`。可执行文件和搜索根目录必须是已有的原生绝对路径。`fixed_arguments` 默认为 `[]`，`safe_base_environment` 为 `{}`，`allow_login_mode` 为 `false`。

## 受控会话出站网络

在守护进程 JSON 中设置 `"egress": {"mode": "controlled"}`，或使用 `A13N_ENVD_EGRESS_MODE=controlled`、`--egress-mode controlled`，即可要求 Linux 受控会话。每个会话必须提供带显式类型的目标策略；inherit 和 deny 会拒绝会话出站网络策略。秘密值通过 EIP 提交，绝不能存入该文件。前置条件、创建、实时更新和容量行为见[会话出站网络](egress.md)。
