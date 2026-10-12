---
title: 沙箱镜像
description: 选择最小 Envd 运行镜像，或带浏览器自动化和可选工作目录 skill 的开发沙箱。
---

`a13n-sandbox` 提供两个变体。两者都以 root 启动 Envd，并以预置的 `sandbox` 账号（`1000:1000`）执行会话命令和文件操作。标准版还授予该账号免密码 sudo。隔离由外层容器或虚拟机提供；标准版的默认 UID 无法阻止主动获取 root 权限。

| 变体           | 内容                                                        | 用途                                                                      |
| -------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| 标准版（默认） | Envd、开发工具链、agent-browser、Chromium、字体和内置 skill | 通过 Envd 或原生 Docker Provider 进行 agent 开发                          |
| Slim           | Envd、Debian 基础 shell/工具、运行库、CA 证书和 tini        | 运行 Envd 或派生自定义镜像；不包含 Python、Node.js、编译器、sudo 或浏览器 |

Slim 不满足原生 Docker Provider 对 Python 的要求，也不包含可选受控出站网络所需的网络工具；请使用标准版，或在派生镜像中安装这些工具。

**受控出站网络默认关闭。** 普通使用无需 `--privileged`、额外 capabilities，也无需禁用 Docker 安全配置。Envd 仍要求 Linux 运行时支持其原生进程操作；自定义运行时或缩减的 capability 集合可能拒绝这些操作。

## 构建与检查

在仓库根目录运行：

```bash
make image-sandbox
make image-check-sandbox
make image-sandbox-slim
make image-check-sandbox-slim
```

本地标签为 `a13n-sandbox:local` 和 `a13n-sandbox:local-slim`。冒烟检查验证两个变体的守护进程启动、身份和目录权限。标准版还检查工具链、sudo、skill 初始化及离线浏览器快照与截图。容器 CI 在原生 AMD64 和 ARM64 runner 上执行这些检查。协议和执行隔离测试仍由 Envd 测试套件负责。

发布镜像使用 `ghcr.io/converge-ai-labs/a13n-sandbox`。选择与 EIP 客户端匹配的 Envd 发布标签。两个变体包含相同版本的守护进程。

| 通道        | 标准版            | Slim                 |
| ----------- | ----------------- | -------------------- |
| 稳定版      | `X.Y.Z`、`latest` | `X.Y.Z-slim`、`slim` |
| 候选版      | `X.Y.Z-rc.N`      | `X.Y.Z-rc.N-slim`    |
| Main 开发版 | `dev`             | `dev-slim`           |

RC 和开发版发布都不会推进 `latest` 或 `slim`。本文介绍当前检出中的源码镜像，不保证较旧的已发布标签已经具有这些默认值。

## 配合 Host 运行

### Stdio 传输

让 EIP Host 通过管道连接 stdin 和 stdout，启动以下命令：

```bash
docker run --rm -i a13n-sandbox:local
```

保留 `-i`，不要添加 `-t`：stdin/stdout 承载 EIP 消息帧，不是交互终端。默认命令在 tini 管理下启动 Envd；标准版入口会先初始化工作目录 skill。它并非 shell、HTTP 监听器或空闲容器。stdin 保持打开时，它等待 EIP 客户端；stdin 关闭时退出。不带 `-i` 运行后正常退出是预期行为。

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

## 工作目录 skill 与浏览器自动化

标准版在构建时安装固定版本的 agent-browser CLI 和系统 Chromium。`AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium` 在两个架构上选择该浏览器。启动时不下载浏览器或 skill。与普通 EIP 命令一样，以 `sandbox` 身份执行浏览器命令。

内置 skill 源目录为 `/usr/local/share/a13n/skills`。默认入口同步运行 `a13n-sandbox-init`，再用 `exec` 启动指定命令。初始化脚本把每个缺失的 skill 目录复制到容器启动 cwd 下的 `.agents/skills`，默认 cwd 为 `/workspace`。可用 Docker `--workdir` 选择其他已有且可写的目录。之后选择另一个 Envd 会话 cwd，或覆盖守护进程的默认工作目录，都不会初始化那个目录。

已有的同名目录、文件和符号链接原样保留，不合并也不更新。升级镜像后，工作目录中已有的 skill 仍由用户管理；需要重新初始化时，请显式移除对应 skill 目录。复制失败时不会发布不完整的 skill。目标不可写时，脚本向 stderr 输出警告，但不会阻止指定命令启动。初始化消息不写入 stdout，避免干扰 stdio EIP。

以 root 调用时，初始化脚本先切换到 `A13N_ENVD_EXECUTION_UID` 和 `A13N_ENVD_EXECUTION_GID` 指定的身份（镜像默认 `1000:1000`），再写入文件。非 root 调用者保留当前身份。脚本不解析 Envd JSON 或 CLI 选项，也不递归修改挂载文件的所有权。选择其他预置账号时，请统一使用镜像的 UID/GID 环境变量。`A13N_SANDBOX_SKILLS_DIR` 可选择其他内置 skill 源目录。

替换 CMD 仍会运行初始化；替换 ENTRYPOINT 则跳过初始化。自定义启动流程和原生 Docker Provider 可以显式调用脚本：

```bash
# 绕过默认入口，以工作负载用户身份显式初始化。
docker run --rm --user sandbox --entrypoint sh a13n-sandbox:local \
  -c 'a13n-sandbox-init && agent-browser skills get core'
```

原生 Docker Provider 会替换镜像入口。需要初始化时，设置已有的 recipe 字段：

```yaml
init_script: a13n-sandbox-init
```

Envd 和 Provider 不会在这一启动约定之外自动安装 skill。启用 Skills capability 后，Harness UI 在准备 Run 时，从所选设备工作目录的 `.agents/skills` 发现 skill。

## 日常开发

标准镜像包含带 pip/venv 的 Python 3.13、uv、Node.js 24、npm、pnpm、C/C++ 构建工具、Bash、Git、curl、SSH 客户端、jq、ripgrep、patch、zip/unzip、进程工具、sudo 和 CA 证书。`sandbox` 可写入 `/workspace` 和 `/tmp/a13n`。镜像还安装了可选出站网络所需的用户态命名空间与网络工具；安装工具并不会授予内核权限。项目专用依赖可通过 sudo 或派生镜像添加。发布镜像支持 `linux/amd64` 和 `linux/arm64`。

标准镜像也用于原生 Docker Environment Provider。该 provider 用保活进程替换守护进程入口，通过 Docker exec 执行文件与 shell 操作。除非 recipe 显式设置 `user`，镜像标签 `ai.a13n.environment.user=sandbox` 会选择非 root 容器用户；独立运行的 Envd 仍以 root 启动，并以 `sandbox` 执行 EIP 操作。原生 Docker 不启动 Envd，也不提供 EIP 监听端口。

通过 EIP 执行的命令以 `sandbox` 身份启动，并设置 `HOME=/home/sandbox`。例如：

```bash
id -u                         # 1000
sudo -n id -u                 # 0
sudo -n apt-get update
sudo -n apt-get install -y sqlite3
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

| 设置       | 镜像行为                                                       |
| ---------- | -------------------------------------------------------------- |
| 启动者     | Root，由 tini 管理                                             |
| 执行身份   | `A13N_ENVD_EXECUTION_UID=1000`、`A13N_ENVD_EXECUTION_GID=1000` |
| 命令       | `A13N_ENVD_FULL_CONTROL=true` 启用原生 shell                   |
| Sudo       | 标准版授予 `sandbox` 免密码 sudo；slim 不安装 sudo             |
| 出站网络   | 独立守护进程默认值为 `inherit`；不表示存在目标过滤             |
| 工作目录   | `/workspace`                                                   |
| 安装状态   | `A13N_ENVD_STATE_DIR=/var/lib/a13n-envd`                       |
| 独立运行时 | `A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state`                   |

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
