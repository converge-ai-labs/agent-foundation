---
title: 沙箱镜像
description: 选择镜像、连接 Host，并在更换容器后保留工作目录。
---

开发和浏览器自动化选择标准 `a13n-sandbox` 镜像，Computer Use 选择桌面版，最小 Envd 运行环境选择 slim。

| 变体           | 内容                                                        | 用途                                                                      |
| -------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| 标准版（默认） | Envd、开发工具链、agent-browser、Chromium、字体和内置 skill | 通过 Envd 或原生 Docker Provider 进行 agent 开发                          |
| 桌面版         | 标准工具链、Xvfb、Openbox 和启动管理                        | 通过镜像内的 Envd 使用 Computer Use                                       |
| Slim           | Envd、Debian 基础 shell/工具、运行库、CA 证书和 tini        | 运行 Envd 或派生自定义镜像；不包含 Python、Node.js、编译器、sudo 或浏览器 |

所有变体都以 root 启动 Envd，以 `sandbox`（`1000:1000`）执行会话命令和文件操作。标准版和桌面版授予该账号免密码 sudo，因此隔离边界由外层容器或虚拟机提供。Slim 用于原生 Docker Provider 时需补装 Python，用于受控出站网络时需补装网络工具。

先使用默认的 `inherit` 出站模式。普通使用无需 `--privileged`、额外 capabilities 或禁用 Docker 安全配置。Linux 运行时需支持 Envd 的原生进程操作；自定义运行时和缩减的 capability 集合可能拒绝这些操作。需要按目标过滤网络访问时，参见[受控出站网络](#opt-in-controlled-egress)。

## 构建与检查

在仓库根目录运行所选变体对应的构建和冒烟检查命令：

```bash
make image-sandbox
make image-check-sandbox
make image-sandbox-slim
make image-check-sandbox-slim
make image-sandbox-desktop
make image-check-sandbox-desktop
```

生成的本地标签为 `a13n-sandbox:local`、`a13n-sandbox:local-slim` 和 `a13n-sandbox:local-desktop`。冒烟检查覆盖守护进程启动、身份和目录权限。标准版还检查工具链、sudo、skill 初始化及离线浏览器快照与截图。容器 CI 使用原生 AMD64 和 ARM64 runner；协议和执行隔离测试由 Envd 测试套件负责。

使用发布镜像时，从 `ghcr.io/converge-ai-labs/a13n-sandbox` 选择与 EIP 客户端对应的 Envd 发布标签。三个变体的守护进程版本相同：

| 通道        | 标准版            | Slim                 | Desktop                    |
| ----------- | ----------------- | -------------------- | -------------------------- |
| 稳定版      | `X.Y.Z`、`latest` | `X.Y.Z-slim`、`slim` | `X.Y.Z-desktop`、`desktop` |
| 候选版      | `X.Y.Z-rc.N`      | `X.Y.Z-rc.N-slim`    | `X.Y.Z-rc.N-desktop`       |
| Main 开发版 | `dev`             | `dev-slim`           | `dev-desktop`              |

`latest`、`slim` 和 `desktop` 跟随稳定版发布；RC 和开发版不会更新这些标签。下文描述当前检出的镜像行为。使用旧标签时，核对该版本的功能和默认值。

## Service 中的托管 Docker 桌面

先配置 Docker 连接，再创建桌面环境：

1. 在所选 Docker Engine 上构建 `a13n-sandbox:local-desktop`，或选择兼容的桌面版发布标签。
2. 在 Console 添加 Docker 连接。在**容器管理**中配置 Engine 访问。开启**环境操作通道**，将**主机访问地址**设为所有 Service / Worker 都能访问的主机名或 IP。
3. 本机进程使用 Docker Desktop 时，访问地址和监听地址使用 `127.0.0.1`。远端 Engine 需配置可达私网地址、监听接口，并显式允许可信私网明文连接。
4. 在模板编辑器中选择**桌面环境**。此预设将桌面镜像和 HTTP EIP 执行方式固定搭配。**标准环境**使用标准镜像和 Docker 原生执行。两者均可配置 CPU、内存和挂载。
5. 使用本地构建或自己的镜像时，选择**自定义镜像**。设置镜像 `a13n-sandbox:local-desktop`、执行方式 `envd_http`、**桌面能力**（`desktop: true`）；本地构建设 `pull_policy: never`。保持网络开启。镜像会启动桌面和 Envd，无需初始化脚本。
6. 创建环境。Service 发布 Envd 端口，读取 Docker 分配的主机端口，认证设备并检查桌面就绪状态。开始任务前，开启 agent 的 Computer 工具及挂载环境所需的 Computer 操作权限。

可信私网可使用以下 Docker 连接配置：

```json
{
  "docker_host": "tcp://10.0.0.8:2376",
  "envd_http": {
    "host": "10.0.0.8",
    "bind_host": "10.0.0.8",
    "allow_plaintext_private_link": true
  }
}
```

允许所有相关 Service / Worker 访问映射到容器 8888 端口的主机随机端口。单独保护 Docker API 传输。Envd HTTP 在可信私网路径上传输 bearer 凭据，这条路径本身不提供 TLS 终止；不要将其暴露到公网。部署的出站策略也会检查最终地址。宿主网络之外的 Worker 需要能入站访问宿主机；此连接不提供反向 WebSocket 隧道。

镜像在 `/var/lib/a13n-envd/http-token`（权限 0600）或配置的 `credential_file` 创建私有随机 token。Provider 通过已授权的 Engine 连接读取它。每次连接都重新检查保留容器的端口映射和预期设备 ID，再直接通过 HTTP EIP 发送 Agent 操作。Token 不进入模板 JSON、公开实例状态或 API 响应。自定义镜像可实现相同启动约定，包括无桌面的 `envd_http` 用法。

按以下方式管理生命周期：

- 就绪失败时，检查保留的容器。
- 关闭 Run 会断开使用，但容器继续运行。
- 停止再启动会保留文件和凭据，并创建新的图形会话。
- 停止或销毁会撤销访问。轮换 token 时，先停止容器，替换私有 token 文件，关闭已有会话，再重新启动。

Root 桌面启动器监督以 `sandbox` 身份运行的 Xvfb/Openbox，创建 Xauthority cookie 并关闭 X11 TCP 监听。必需的桌面进程退出会让容器失败。X11 支持按键事件，尚不支持字面 Unicode `type_text`。

人工访问需单独启用。设置 `desktop_assistance: true`，并提供浏览器可访问、路由到容器 6081 端口的 `desktop_viewer_url`。Service 开启镜像的 `A13N_DESKTOP_ACCESS` supervisor；运维者提供 viewer 路由和 TLS。权限和交还控制的流程见[桌面协助](../a13n-service/environments.md#agent-requested-desktop-assistance)。

## 配合 Host 运行

### Stdio 传输

配置 EIP Host，通过管道连接 stdin 和 stdout 来启动以下命令：

```bash
docker run --rm -i a13n-sandbox:local
```

保留 `-i`，省略 `-t`，让管道直接承载 EIP 消息帧，不经过终端处理。入口先初始化工作目录 skill，再由 tini 管理 Envd。stdin 保持打开时，Envd 等待 EIP 客户端；stdin 关闭后退出。不带 `-i` 时立即正常退出是预期行为。

在默认工作目录挂载 Docker 管理的卷，以便删除容器后保留文件：

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local
```

新建空卷继承镜像目录的所有权。已有卷或绑定挂载需为 `1000:1000` 配好写入权限；启动过程保留原所有权。关闭会话会保留工作目录文件。删除容器会丢弃卷和绑定挂载之外的文件。

### 连接 Harness UI

分别用两个卷保存设备凭据和工作目录文件，再向 Harness UI 发起出站连接：

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local \
  a13n-envd connect https://host.example.com --host work
```

1. 将 URL 替换为容器内部可访问的地址。容器的 `localhost` 指向容器自身。
2. 在 Host 批准打印的验证码，并保持容器运行。出站连接模式无需发布 Docker 端口。
3. 后续启动复用状态卷，以保留设备身份和凭据。凭据卷只供这个沙箱使用。

参见[注册与保存的 Host](configuration.md#connect-to-harness-ui)。接入 Service 时，运行 HTTP 守护进程并[注册外部目标](../environments/remote-envd.md#connect-to-the-service)。

安装状态位于 `/var/lib/a13n-envd`。独立运行时的代次私有数据使用 `/run/a13n-envd-state`；`connect` 在安装目录下保存各 Host 的运行时数据。通过工作目录卷保留工作成果，而不是依赖这些运行时目录。

## 工作目录 skill 与浏览器自动化

标准版在构建时装入固定版本的 agent-browser CLI、系统 Chromium 和内置 skill。`AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium` 在两个架构上选择 Chromium。以 `sandbox` 身份运行浏览器命令；启动时直接使用已安装的资源，不下载它们。

启动时，`a13n-sandbox-init` 将 `/usr/local/share/a13n/skills` 中缺失的 skill 目录复制到容器初始 cwd 下的 `.agents/skills`，默认 cwd 为 `/workspace`。入口等待初始化完成，再用 `exec` 启动指定命令。用 Docker `--workdir` 可初始化另一个已有且可写的目录。之后更改 Envd 会话 cwd 不会初始化新目录。

已有同名项保持原样，包括文件和符号链接。因此升级镜像会保留工作目录中的 skill；需要重新填充时，显式移除旧 skill 目录。复制失败不会留下对外可见的不完整 skill。目标不可写时会向 stderr 输出警告，并继续启动。初始化消息写入 stderr，以保留 stdio 协议通道。

使用其他执行账号时，一致地设置 `A13N_ENVD_EXECUTION_UID` 和 `A13N_ENVD_EXECUTION_GID`。Root 初始化脚本在写入前切换到这些 ID；非 root 调用者保留自身身份。初始化脚本读取这些环境变量，不解析 Envd JSON 或 CLI 选项，并保留挂载文件的所有权。设置 `A13N_SANDBOX_SKILLS_DIR` 可选择其他内置源目录。

替换 CMD 会保留初始化。替换 ENTRYPOINT 会跳过初始化，因此自定义启动器应显式调用：

```bash
# 绕过默认入口，以工作负载用户身份显式初始化。
docker run --rm --user sandbox --entrypoint sh a13n-sandbox:local \
  -c 'a13n-sandbox-init && agent-browser skills get core'
```

原生 Docker Provider 会替换入口。添加以下 recipe 设置来初始化 skill：

```yaml
init_script: a13n-sandbox-init
```

启用 Skills capability 后，Harness UI 在准备 Run 时从所选设备工作目录的 `.agents/skills` 发现 skill。这些文件由启动初始化脚本提供；Envd 和 Provider 不会另外安装它们。

## 日常开发

标准工具链包含带 pip/venv 的 Python 3.13、uv、Node.js 24、npm、pnpm、C/C++ 构建工具、Bash、Git、curl、SSH 客户端、jq、ripgrep、patch、zip/unzip、进程工具、sudo 和 CA 证书。`sandbox` 可写入 `/workspace` 和 `/tmp/a13n`。镜像也安装了受控出站网络所需的用户态命名空间与网络工具；内核权限由部署提供。发布镜像支持 `linux/amd64` 和 `linux/arm64`。

原生 Docker 执行时，Provider 用保活进程替换守护进程入口，通过 Docker exec 操作文件和 shell。除非 recipe 设置了 `user`，镜像标签 `ai.a13n.environment.user=sandbox` 会选择执行用户。这条路径不运行 Envd，也不提供 EIP 监听器。

EIP 执行时，命令以 `sandbox` 启动，`HOME=/home/sandbox`。可用 sudo 安装系统依赖，在虚拟环境中安装 Python 依赖：

```bash
id -u                         # 1000
sudo -n id -u                 # 0
sudo -n apt-get update
sudo -n apt-get install -y sqlite3
python3 -m venv .venv
.venv/bin/python -m pip install requests
```

系统软件包修改随该容器保留。将需要重复使用的依赖放入派生镜像，并为工作负载配置 Git 作者身份和 SSH 凭据。执行 sudo 命令后，文件 RPC 仍使用非特权账号；通过 RPC 编辑 root 创建的文件前，先调整权限或所有权。

以工作负载账号调试运行中的容器：

```bash
docker exec -it --user sandbox agent-sandbox bash
```

直接 Docker exec 使用 Docker 的权限，不受 Envd 身份、sudo 和出站策略约束。省略 `--user sandbox` 会选择镜像的 root 启动账号。

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

保留默认命令，通过环境变量覆盖设置。例如，阻止 Envd worker 及其后代进程提权：

```bash
docker run --rm -i \
  -e A13N_ENVD_ALLOW_SUDO=false \
  a13n-sandbox:local
```

这会禁用 setuid/文件 capability 提权，包括原生 sudo。守护进程仍以 root 启动，Docker 管理员保留其权限。只需文件操作时，再设置 `A13N_ENVD_FULL_CONTROL=false`。

使用其他账号时，在派生镜像中预置账号，设置两个执行 ID 并安排文件系统权限。只修改 Docker `--user` 可能留下不兼容的执行 ID 或 root 所有的状态路径。

镜像环境变量优先于守护进程 JSON。修改对应环境变量或提供 CLI 选项；参见[配置优先级与原生身份](configuration.md)。

### 主动启用受控出站网络

在部署和会话两个层面启用目标过滤：

1. 准备兼容的 Linux 运行时及[所需的命名空间/内核能力](egress.md)。普通容器内的 root 权限不足以满足要求。
2. 以 `A13N_ENVD_EGRESS_MODE=controlled` 启动 Envd。
3. 由可信 Host 在 `session.open` 中提供 `egress` 策略。

缺少运行时能力可能导致启动或会话准备失败。Envd 会拒绝不匹配的请求：`inherit` 模式拒绝附带策略的请求，`controlled` 模式要求提供策略。镜像不会自行提权，也不会回退到无过滤执行。
