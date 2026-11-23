---
title: 沙箱镜像
description: 构建并运行官方 a13n-sandbox 容器镜像，内置 Envd、非 root 执行、可选桌面支持以及 Service/Harness UI 集成。
---

`ghcr.io/converge-ai-labs/a13n-sandbox` 是用于运行 `a13n-envd` 的预构建容器镜像，内置 Python、Node.js、Git 及拥有 `/workspace` 权限的非 root `sandbox` 用户。

## 本地运行

```sh
docker run --rm -it ghcr.io/converge-ai-labs/a13n-sandbox:dev
```

或从源码构建：

```sh
make image-sandbox SANDBOX_IMAGE=a13n-sandbox:local
```

## Service 中的托管 Docker 桌面

如需在 Docker 中运行可观测桌面，请构建或选用桌面镜像（`a13n-sandbox:local-desktop`），并在 Service 模板中配置 `execution="envd_http"`、`desktop=true` 并保持网络开启。

- **人工桌面协助：** 设置 `desktop_assistance: true` 并将浏览器可达的 `desktop_viewer_url` 路由到容器 `6081` 端口。权限与交接流程见 [Service 计算机使用](../a13n-service/computer-use.md#create-a-docker-desktop)。
- **独立注册：** 启动 HTTP 守护进程并[注册外部目标](../environments/remote-envd.md#connect-to-the-service)，或通过[出站 Envd](configuration.md#connect-to-harness-ui) 连接到 Harness UI。
