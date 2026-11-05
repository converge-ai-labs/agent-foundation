---
title: 配置工作空间自动资源
description: 自动配置默认执行资源，并选择部署使用的沙盒镜像。
---

按本部署配方为工作空间自动创建默认 Local 或 Docker 执行资源。自动配置仅在 all 角色运行；拆分 control/worker 的部署须显式配置资源。启用 Docker 自动配置前先连接 Engine，资源创建后再验证实际环境。

## 工作空间自动配置

Local/Docker provider 和模板的自动创建只在 `all` 角色运行。通用设置中两者默认关闭；提供的挂载 socket 的 Compose 部署启用 Docker，关闭 Local。分离的 control/worker 部署需要明确配置执行资源。

初始化 Docker 时，先让 Service 进程通过 `environments.docker_host` 或其 Docker 环境访问目标 Engine。只安装 CLI 不会获得 Engine 访问权限。然后启用：

```toml
[provisioning.docker]
enabled = true
```

默认模板使用 Service 审定的 sandbox 镜像和 `pull_policy = "if_missing"`。自动配置只创建一次资源，初始化时不拉取镜像或创建容器；实例需要镜像时才拉取。后续设置变化不覆盖已有模板，需要在 Console 或通过 API 编辑。已有实例保留原镜像。

在仓库检出中使用本地构建镜像：

1. 在仓库根目录运行 `make image-sandbox`。
2. 将以下覆盖配置保存为该根目录下的 `compose.local.yaml`：

```yaml
services:
  service:
    environment:
      A13N_PROVISIONING__DOCKER: '{"enabled":true,"image":"a13n-sandbox:local","pull_policy":"never"}'
```

3. 同时加载两个文件启动：

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml -f compose.local.yaml up -d --wait
```

直接使用 TOML 部署时，将同样的 `image` 和 `pull_policy` 字段放入 `[provisioning.docker]`。镜像仓库中的镜像使用 `if_missing`。环境变量覆盖完整 JSON 对象；旧的 `A13N_DOCKER_ENVIRONMENT_IMAGE` 快捷变量不再读取。

仅在开发部署中使用 Local 执行，并指定绝对根目录：

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"
```

Local 直接在 worker 宿主机执行，没有隔离。禁用后保留目录和资源，但后续执行无法再使用 Local。禁用 Docker 自动配置后，手动配置的 Docker provider 仍可使用。重试和职责规则见[工作空间自动配置](environments.md#workspace-provisioning)。
