---
title: 沙箱镜像
description: 运行官方 a13n-envd 容器镜像（standard、slim、desktop 与 VM 变体），支持持久化工作区卷与非 root 执行。
---

`ghcr.io/converge-ai-labs/a13n-sandbox` 提供三种 `a13n-envd` 容器镜像变体（标签对应 `X.Y.Z` / `latest`、`-slim`、`-desktop`，开发构建为 `dev*`）：

| 变体       | 内置组件                                                                                     | 典型用途                                                  |
| ---------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `standard` | Envd、Python 3.13、uv、Node 24、pnpm、Git、C/C++ 工具链、Chromium、`agent-browser`、内置技能 | 默认编码与浏览器自动化沙箱（`sandbox` 1000:1000）         |
| `desktop`  | 标准版工具链 + 受监管的 Xvfb 与 Openbox 桌面                                                 | GUI 计算机使用（`execution="envd_http"`, `desktop=true`） |
| `slim`     | Envd、基础 Shell 工具、CA 证书与 tini（不含 Python、Node、sudo 或浏览器）                    | 轻量文件/命令运行时或自定义基础镜像                       |

在仓库根目录可通过 `make image-sandbox`（`-slim`、`-desktop`）与 `make image-check-sandbox` 本地构建并执行冒烟检查。

## 独立运行或连接 Harness UI

- **宿主托管 stdio（保留 `-i`，不要加 `-t`）：**

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  ghcr.io/converge-ai-labs/a13n-sandbox:latest
```

- **主动出站连接 Harness UI：**

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  ghcr.io/converge-ai-labs/a13n-sandbox:latest \
  a13n-envd connect https://host.example.com --host work
```

配对步骤见[连接到 Harness UI](configuration.md#connect-to-harness-ui)；对接 Service 可运行 HTTP 守护进程并[注册外部目标](../environments/remote-envd.md#connect-to-the-service)。

## Service 中的托管 Docker 桌面

使用 `a13n-sandbox:local-desktop` 或已发布的 `-desktop` 标签，在模板中设置 `execution="envd_http"` 与 `desktop=true`（见 [Service 计算机使用](../a13n-service/computer-use.md#create-a-docker-desktop)）。

- 镜像自动在 `/var/lib/a13n-envd/http-token`（`0600`）生成私有令牌，并在容器 `8888` 端口监管 Xvfb、Openbox 与 Envd。
- 启用人工实时协助（`desktop_assistance: true`）时，将浏览器可达的 `desktop_viewer_url` 路由到容器 `6081` 端口（见[桌面控制前提](../a13n-service/computer-use.md#desktop-access-prerequisites)）。

## 默认行为、技能初始化与覆盖项

- **身份与 sudo：** 以 `tini` + root 启动守护进程，并以 `sandbox`（`1000:1000`，默认目录 `/workspace`）执行命令和文件操作。标准版与桌面版默认允许免密 `sudo`；传入 `-e A13N_ENVD_ALLOW_SUDO=false` 可通过 `no_new_privs` 禁止提权。
- **工作区技能种子：** 启动时 `a13n-sandbox-init` 会将缺失的内置技能复制到 `/workspace/.agents/skills`（若使用替换入口点的原生 Docker 提供方配方，可配置 `init_script: a13n-sandbox-init`）。
- **受控出站：** 在具备[所需命名空间能力](egress.md)的 Linux 运行时上设置 `A13N_ENVD_EGRESS_MODE=controlled`，并在 `session.open` 中下发 `egress` 策略。
