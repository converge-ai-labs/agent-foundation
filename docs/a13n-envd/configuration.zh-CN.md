---
title: 守护进程配置与传输
sidebarTitle: 配置与传输
description: 配置 Envd 的 stdio、HTTP 和反向 WebSocket 传输、会话默认值、命令启用以及系统遥测采集。
---

在启动 `a13n-envd` 时配置传输方式、允许执行的命令以及运行所用的系统账号。如需开箱即用的沙箱容器镜像，请参阅[沙箱镜像](sandbox.md)。

## 连接到 Service

使用具备工作区 `write` 权限的 Service API Token（保存在权限为 `0600` 的文件中）注册计算机：

```sh
a13n-envd connect-service https://control.example.com --host work \
  --workspace-id ws_example --api-token-file /private/path/api-token \
  --default-working-directory /path/to/project
```

首次运行会完成注册，并将专用设备凭据保存到 `--state-dir`（默认 `~/.a13n-envd`）。后续重启只需运行 `a13n-envd connect-service work`，不再需要 API Token 文件。

若本地设备凭据丢失，调用 `POST /api/v1/client-environments/{id}/rotate-credential?workspace_id=…`（携带 `If-Match`）轮换凭据，将新 Token 写入私密文件后，通过 `--credential-file /private/path/device-token` 重启即可。

## 连接到 Harness UI

从执行任务的计算机发起出站连接：

```console
a13n-envd connect https://host.example.com --host work
```

1. 打开终端打印的审批地址并登录宿主。
2. 核对并批准验证码。
3. 保持 Envd 运行；后续重启执行 `a13n-envd connect work` 即可复用注册。

除回环地址（如 `http://127.0.0.1:8765`）外均要求 HTTPS。私有 CA 使用 `--ca-file`，显示名称使用 `--name`，默认工作目录使用 `--default-working-directory`。

### 已保存的实例与宿主

Envd 将设备 ID 与各宿主凭据保存在 `--state-dir`（`A13N_ENVD_STATE_DIR`，Unix 默认 `~/.a13n-envd`，Windows 默认 `%LOCALAPPDATA%/.a13n-envd`）。

如需在同一台机器上连接两个不同宿主，请分别使用独立的 `--instance` 与 `--host` 启动两个进程：

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

重启时请保留状态目录，并将其视同账号凭据妥善保护。

## 启用命令

默认情况下 Envd 仅开放文件操作。启用 Full Control 可直接以执行账号权限开放 `/bin/sh`（Unix）或 PowerShell（Windows）：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

也可以保持 `full_control` 关闭，在守护进程 JSON 中显式声明 `trusted_executable_roots` 与 `shell_profiles`：

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
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576
    }
  ]
}
```

## 配置层级

<span id="minimal-standalone-configuration"></span>

Envd 按优先级从低到高合并五层配置（对象递归合并，标量与数组直接覆盖）：

1. 内置默认值
2. `--config /path/to/a13n-envd.json`
3. `A13N_ENVD_CONFIG_JSON`
4. 标量环境变量 `A13N_ENVD_*`（如 `A13N_ENVD_FULL_CONTROL=true`、`A13N_ENVD_DEVICE_ID=...`、`A13N_ENVD_ALLOW_SUDO=false`）
5. 显式 CLI 参数

## 传输配置

通过 `A13N_ENVD_TRANSPORT` 选择传输模式：

| 模式                | 连接方向          | 典型场景                        |
| ------------------- | ----------------- | ------------------------------- |
| `stdio`             | 父进程管道        | 宿主托管的本地守护进程（默认）  |
| `http`              | 宿主直连 Envd     | HTTPS 或回环 HTTP 守护进程      |
| `reverse_websocket` | Envd 主动连接宿主 | NAT/防火墙后的出站连接（`wss`） |

本地回环 HTTP 启动示例：

```bash
mkdir -p "$HOME/.a13n-envd-runtime" && chmod 700 "$HOME/.a13n-envd-runtime"
export A13N_ENVD_RUNTIME_DIR="$HOME/.a13n-envd-runtime"
export A13N_ENVD_DEVICE_ID=device-http-example
export A13N_ENVD_TRANSPORT=http
export A13N_ENVD_HTTP_BIND=127.0.0.1:8787
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/private/envd-token
export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback
a13n-envd --default-working-directory /absolute/path/to/workspace
```

启用 HTTPS 时，取消 `A13N_ENVD_HTTP_PLAINTEXT_SCOPE`，改为设置 `A13N_ENVD_HTTP_TLS_CERT_FILE` 和 `A13N_ENVD_HTTP_TLS_KEY_FILE`。使用 `reverse_websocket` 时，设置 `A13N_ENVD_REVERSE_WS_URL` 和 `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`。

## 守护进程生命周期与重启

<span id="lifecycle-and-ownership"></span>

| 部署方式                         | 命令                            | 重启负责方                    |
| -------------------------------- | ------------------------------- | ----------------------------- |
| 外部服务管理器或宿主托管 `stdio` | `a13n-envd daemon [options]`    | 服务管理器或宿主              |
| 独立 HTTP / 反向 WebSocket       | `a13n-envd supervise [options]` | 内置监管进程（最多重启 3 次） |

重启会保留 Device ID、工作区文件和凭据，但会开启新世代——客户端需要重新打开 Session。

## 原生身份与 sudo

在 Linux 上，Envd 默认以启动用户身份运行。若要从 root 降权至已预置的非 root 账号，需同时设置 `execution.uid` 与 `execution.gid`（或 `A13N_ENVD_EXECUTION_UID` 与 `A13N_ENVD_EXECUTION_GID`）。

> [!IMPORTANT]
> `execution.allow_sudo` 默认为 `true`（仍受镜像 sudoers 约束）。在 Linux 上传入 `--allow-sudo false` 或设置 `A13N_ENVD_ALLOW_SUDO=false` 可通过 `no_new_privs` 禁止提权。

## 健康探针与监控

### 可选健康探针

启用无鉴权的 HTTP 存活探针（`GET /healthz` 或 `/ping`，返回 `200 ok`）：

```bash
A13N_ENVD_HEALTH_ENABLED=true A13N_ENVD_HEALTH_BIND=127.0.0.1:8080 a13n-envd daemon
```

### 系统信息与资源监控

Envd 支持采集宿主机/容器的 CPU、内存与磁盘指标，并通过 Prometheus 或 OpenTelemetry 导出。配置方式见[监控与指标](monitoring.md)。

## 独立配置字段参考

| JSON 字段                                    | 默认值 / 说明                                                           |
| -------------------------------------------- | ----------------------------------------------------------------------- |
| `device_id`                                  | 稳定设备标识（默认由安装状态目录持久化生成）                            |
| `default_working_directory`                  | 会话初始工作目录（默认继承守护进程启动目录）                            |
| `directory_discovery`                        | `true` —— 允许在打开会话前进行受限目录浏览                              |
| `idle_timeout_ms` / `max_lifetime_ms`        | 会话默认空闲超时（`3600000` 毫秒）与最大存活时间                        |
| `vm_startup_timeout_ms`                      | `120000` 毫秒 —— VM 启动准入超时                                        |
| `execution.uid`、`execution.gid`             | 可选的成对 Linux UID/GID                                                |
| `execution.allow_sudo`                       | `true` —— 在 Linux 上设为 `false` 可强制启用 `no_new_privs`             |
| `sandbox`                                    | `{"mode":"disabled"}` 或带目录 `grants` 的 `restricted`                 |
| `egress`                                     | `{"mode":"inherit"}`、`deny` 或 `controlled`（见[会话出站](egress.md)） |
| `full_control`                               | `false` —— 启用原生 Shell 与继承环境                                    |
| `computer_use`                               | `false` —— 按需启用桌面控制（见[计算机使用](computer-use.md)）          |
| `trusted_executable_roots`、`shell_profiles` | `full_control` 为 `false` 时的显式命令白名单                            |
| `limits`                                     | 设备与单会话并发数、文件大小及输出缓存上限                              |
