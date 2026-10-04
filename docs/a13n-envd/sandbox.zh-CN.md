---
title: 沙箱镜像
description: 内置 Envd 和支持 sudo 的 sandbox 账号，可直接用于 agent 开发的 Linux 容器。
---

在 `a13n-sandbox` 镜像中，Envd 以 root 启动；会话命令和文件操作以预置的 `sandbox` 账号（`1000:1000`）运行。该账号支持免密码 sudo，可安装软件包并管理这个临时容器。隔离由外层容器或虚拟机提供；默认 UID 无法阻止主动获取 root 权限。

**受控出站网络默认关闭。** 普通使用无需 `--privileged`、额外 capabilities，也无需禁用 Docker 安全配置。Envd 仍要求 Linux 运行时支持其原生进程操作；自定义运行时或缩减的 capability 集合可能拒绝这些操作。

## 构建与检查

在仓库根目录运行：

```bash
make image-sandbox
make image-check-sandbox
```

本地标签为 `a13n-sandbox:local`。镜像冒烟检查验证启动默认值、`sandbox` 账号的目录权限和免密码 sudo，以及 Docker 默认权限下的守护进程启动。协议和执行隔离测试仍由 Envd 测试套件负责。

发布镜像使用 `ghcr.io/converge-ai-labs/a13n-sandbox`。选择与 EIP 客户端匹配的 Envd 发布标签；`dev` 跟随 main，稳定版本使用 `X.Y.Z`，RC 使用 `X.Y.Z-rc.N`，不会推进 `latest`。本文介绍当前检出中的源码镜像，不保证较旧的已发布标签已经具有这些默认值。

## 配合 Host 运行

### Stdio 传输

让 EIP Host 通过管道连接 stdin 和 stdout，启动以下命令：

```bash
docker run --rm -i a13n-sandbox:local
```

保留 `-i`，不要添加 `-t`：stdin/stdout 承载 EIP 消息帧，不是交互终端。默认命令是 `tini -- a13n-envd`，并非 shell、HTTP 监听器或空闲容器。stdin 保持打开时，它等待 EIP 客户端；stdin 关闭时退出。不带 `-i` 运行后正常退出是预期行为。

默认工作目录为 `/workspace`。使用 Docker 管理的卷保留工作成果：

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local
```

新建的空卷继承镜像目录的所有权。已有卷和绑定挂载保留原所有权；请自行安排 `1000:1000` 的写入权限。镜像启动时不会递归 chown 挂载文件。关闭会话不会删除 `/workspace` 中的文件，但移除容器会丢弃未存入卷或绑定挂载的文件。

### 连接 Harness UI

通过出站连接接入 Harness UI：

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local \
  a13n-envd connect https://host.example.com --host work
```

将 Host URL 换成**容器内部可以访问** 的地址。容器的 `localhost` 不是宿主计算机。在 Host 上批准打印的验证码，然后保持容器运行。这种出站模式无需发布 Docker 端口。后续启动会复用状态卷中保存的设备身份和凭据。把该卷视为凭据，不要将其挂载到其他无关沙箱。参见[注册与保存的 Host](configuration.md#connect-to-harness-ui)。要配合 Service 使用沙箱，请以 HTTP 守护进程方式运行，并[注册为外部目标](../environments/remote-envd.md#connect-to-the-service)。

持久安装目录为 `/var/lib/a13n-envd`。独立运行时的代次私有数据位于 `/run/a13n-envd-state`；`connect` 在安装目录下管理对应 Host 的运行时。两种运行时目录都不是工作目录或会话恢复检查点。

## 日常开发

镜像包含 Bash、Git、curl、SSH 客户端、jq、ripgrep、patch、zip/unzip、带 pip/venv 的 Python、进程工具、sudo 和 CA 证书。镜像还安装了可选出站网络所需的用户态命名空间与网络工具；安装工具并不会授予内核权限。编译器、Node.js 和项目专用运行时可通过 sudo 或派生镜像添加，不必为所有项目强制指定同一版本。

通过 EIP 执行的命令以 `sandbox` 身份启动，并设置 `HOME=/home/sandbox`。例如：

```bash
id -u                         # 1000
sudo -n id -u                 # 0
sudo -n apt-get update
sudo -n apt-get install -y build-essential
python3 -m venv .venv
.venv/bin/python -m pip install requests
```

系统软件包修改在同一容器的多个会话之间保留，但容器移除后不再保留。需要可重复的依赖时，使用派生镜像。镜像不提供 Git 作者身份和 SSH 凭据；请按自己的工作负载配置。

即使命令使用过 sudo，文件 RPC 仍以非特权身份运行。sudo 创建的 root 所有文件，可能需要显式修改权限或所有权，后续文件 RPC 才能修改。

人工调试已经运行的容器时，显式选择执行账号：

```bash
docker exec -it --user sandbox agent-sandbox bash
```

Docker exec 不经过 Envd：省略 `--user sandbox` 会使用镜像的 root 启动账号，Envd 的身份、sudo 和出站网络策略不会约束这些直接 Docker 操作。

## 默认值与覆盖

| 设置       | 镜像行为                                                            |
| ---------- | ------------------------------------------------------------------- |
| 启动者     | Root，由 tini 管理                                                  |
| 执行身份   | `A13N_ENVD_EXECUTION_UID=1000`、`A13N_ENVD_EXECUTION_GID=1000`      |
| 命令       | `A13N_ENVD_FULL_CONTROL=true` 启用原生 shell                        |
| Sudo       | 独立守护进程默认值允许提权；镜像 sudoers 授予 `sandbox` 免密码 sudo |
| 出站网络   | 独立守护进程默认值为 `inherit`；不表示存在目标过滤                  |
| 工作目录   | `/workspace`                                                        |
| 安装状态   | `A13N_ENVD_STATE_DIR=/var/lib/a13n-envd`                            |
| 独立运行时 | `A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state`                        |

覆盖环境变量无需替换默认命令。例如，禁止 Envd worker 及其后代进程提权：

```bash
docker run --rm -i \
  -e A13N_ENVD_ALLOW_SUDO=false \
  a13n-sandbox:local
```

这会阻止 worker 通过 setuid 或文件 capabilities 提权，包括原生 sudo；它不会改变守护进程的 root 启动身份，也不会限制 Docker 管理员。只需文件操作时，设置 `A13N_ENVD_FULL_CONTROL=false`。要使用其他账号，在派生镜像中预置账号，同时设置执行 UID/GID 并安排文件系统权限。不要只用 Docker `--user` 修改守护进程身份，却保留不兼容的执行 ID 或 root 所有的状态路径。

镜像环境变量优先于守护进程 JSON。修改这些默认值时，覆盖相应环境变量或使用命令行选项。参见[配置优先级与原生身份](configuration.md)。

### 主动启用受控出站网络

受控出站网络**同时** 需要准备好的部署和逐会话请求：

1. 选择兼容的 Linux 运行时，授予[必需的命名空间和内核能力](egress.md)。普通 Docker 容器内的 root 权限并不够。
2. 启动守护进程时设置 `A13N_ENVD_EGRESS_MODE=controlled`。
3. 让可信 Host 在 `session.open` 中提供 `egress` 策略。

镜像不会自行授予 Docker capabilities、自动提权或悄悄回退。缺少能力可能导致守护进程启动或会话准备失败。默认 inherit 模式的守护进程会拒绝提供出站网络策略的会话，不会丢弃策略后继续执行。controlled 模式的守护进程也会拒绝未提供策略的会话。默认 inherit 模式镜像无需通用的 `--privileged` 启动。
