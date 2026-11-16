---
title: Provider 与运行时配置
sidebarTitle: Provider 与运行时
description: 准备后端并连接执行，将目标管理与运行时清理分开。
---

Provider 配置分为三部分：目标配置描述要使用的目标，账号设置和凭据用于访问后端，运行时持有共享客户端。先[选择后端](index.md#choose-a-backend)，再按下面对应的流程设置。要添加自己的适配器，使用 [Provider 插件](provider-plugins.md)。

## 内置 provider

在[后端选择表](index.md#choose-a-backend)选择后端与 Native / Envd 执行路径。创建、停止、恢复和删除属于目标管理；本页说明 Host 借用的运行时客户端。云账号、桌面准备与后端限制见[云 Provider](cloud-providers.md)，可信插件见[扩展 Provider 目录](provider-plugins.md)。

共享 Device 运行时由 Host 保持到所有借用者结束。每次 Envd execution 打开独立的固定 cwd Session；Session 拥有执行资源，Host 仍负责租户授权与容器分配。部署 Sandbox 或受控出站网络前，检查[当前先决条件并验证镜像](../a13n-envd/egress.md#verify-a-sandbox-image)。

## Local Envd 运行时

安装兼容的 `a13n-envd` 二进制文件，再构建 Host 拥有的运行时：

```python
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

解析器依次检查显式路径、`A13N_ENVD_EXECUTABLE`，以及 `PATH` 中平台对应的 `a13n-envd` 可执行文件。在 Host 进程中设置环境变量；库不读取 `.env`、安装二进制文件或回退到 Direct Local。

将 `runtime` 传给 Local Envd connector。首次获取时启动一个共享 Device，并检查守护进程与客户端的精确兼容性。每个打开的执行获得独立 Session。使用后关闭执行，在 Host 关闭时调用 `await runtime.close()`。

在获取 Device 前选择启动 Sandbox 和出站网络设置。Envd 在 Host 账号、容器或虚拟机提供的外层边界内实施这些设置；构建运行时不会探测原生隔离能力。按 [Local Envd 示例](examples.md#local-envd)操作，或用 `make local-envd-test` 运行真实 provider 路径。

## Docker 运行时

配置托管 Docker 目标时：

1. 将 `DockerConnectionConfiguration.docker_host` 设为 Host 可访问的 Engine。后续重连需访问同一 Engine。Service 的 [Compose 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)展示了非 root 进程如何访问 socket。
2. 选择镜像和执行路径。Service 的 `DockerRecipe` 默认采用 `execution="native"` 和 `ghcr.io/converge-ai-labs/a13n-sandbox:dev`。Native 执行使用 Docker exec；`execution="envd_http"` 使用镜像内的 Envd 守护进程。
3. 添加资源限制和可选绑定挂载。挂载 `source` 必须已存在于 Engine 所在机器上；`read_only` 默认为 true。保留容器自身文件系统中的 `/workspace` 和私有命令元数据。命名卷不是配方选项。
4. 创建目标、保存状态并打开 connector。拉取或重新构建镜像标签不会替换已有容器。

默认 `pull_policy="if_missing"` 只拉取缺失镜像。`"never"` 在镜像缺失时报告 `environment_image_missing`。需要精确镜像身份时使用不可变 digest。Native 执行按以下顺序选择用户：显式配方 `user`、`ai.a13n.environment.user`、镜像 USER。标准沙箱镜像选择 UID/GID 1000。

自定义 Native 镜像需要 Linux、Python 3.10+、配置的 shell，以及可写的 `/workspace` 和 `/tmp/a13n`。git-ignore 查询需要 Git。`init_script` 在新容器上运行，初始化失败不会隐式重放。它的 helper 请求超时与普通 shell 时长分开。普通变量放在 `environment`，秘密留在配方字段之外。`memory_gb` 使用十进制 GB，`cpus` 使用核数。`disable_network=true` 选择 Docker 的 `none` 网络。销毁会保留外部绑定挂载的数据。

使用 HTTP EIP 时，在连接的 `envd_http` 中配置 Worker 可达的主机和监听地址。Service 保留镜像入口，将容器 8888 端口发布到 Docker 分配的主机端口。使用兼容的 Envd 镜像并保持网络启用。镜像负责执行身份，因此该路径拒绝配方中的 `user` 覆盖。添加 `desktop: true`，可让就绪检查包含可观察、可输入的桌面。明文 HTTP 要求回环地址或显式信任的私有链路。Console 步骤见[创建 Docker 桌面](../a13n-service/computer-use.md#create-a-docker-desktop)。

无桌面 Envd 可选择与部署版本匹配的官方 `-slim` 沙箱标签，并设置 `execution="envd_http"`、`desktop=false`。管理初始化和凭据读取使用 POSIX shell 与核心工具，不需要 guest Python 或 jq。Native Docker 文件操作仍需要 Python。

使用自定义 base 时，先构建显式兼容的 wrapper 镜像：安装兼容的 Envd 二进制和公共 `a13n-sandbox-entrypoint`，创建 Session 用户与可写工作目录，并在 Dockerfile 中选择该 wrapper 和 daemon 命令。Service 保留入口与命令，以 root 启动。普通 base 镜像本身不是自动安装请求。二进制架构与 userspace 必须匹配；添加 shell 脚本不能让 glibc 二进制在 musl 上运行。桌面 wrapper 还需要桌面启动器及其依赖。

嵌入式 Host 也可以借用自己的 Engine 客户端：

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

在所有借用者结束前保持 Engine 开启，由 Host 在关闭时清理。Registry 认证、credential helper、镜像与代理使用 Docker 客户端配置。

Native 执行替换镜像 entrypoint、启用 Docker init，并在 Run 之间保持容器运行。相对工具路径从 `/workspace` 开始，绝对路径指向容器；Harness 再添加聚合挂载前缀。关闭 execution 只释放本地观测，保留容器与后台进程。重新进入使用保存的容器，确认目标不存在时必须显式管理，传输故障本身不证明目标已消失。

Native Docker 提供文件、shell、进程控制和输出观测；端口观测与 Computer 需要 `envd_http`。现有 Native 目标修改 recipe 不会自动安装守护进程。HTTP EIP 的 Session 关闭保留桌面，停止再启动保留文件并重建 GUI。镜像启动和权限见[托管桌面镜像约定](../a13n-envd/sandbox.md#managed-docker-desktop-in-service)。

## 云 provider

继续阅读[云 Provider 设置与限制](cloud-providers.md#cloud-providers)。

### 重连与生命周期

继续阅读[云 Provider 设置与限制](cloud-providers.md#reconnection-and-lifecycle)。

### E2B 运行时

继续阅读[云 Provider 设置与限制](cloud-providers.md#e2b-runtime)。

### E2B 桌面准备 {#e2b-desktop-preparation}

继续阅读[云 Provider 设置与限制](cloud-providers.md#e2b-desktop-preparation)。

### Daytona

继续阅读[云 Provider 设置与限制](cloud-providers.md#daytona)。

### Modal

继续阅读[云 Provider 设置与限制](cloud-providers.md#modal)。

### Vercel Sandbox

继续阅读[云 Provider 设置与限制](cloud-providers.md#vercel-sandbox)。

### Fly.io Sprites

继续阅读[云 Provider 设置与限制](cloud-providers.md#flyio-sprites)。

### Runloop

继续阅读[云 Provider 设置与限制](cloud-providers.md#runloop)。

### boxd

继续阅读[云 Provider 设置与限制](cloud-providers.md#boxd)。

### 嵌入式配置

继续阅读[云 Provider 设置与限制](cloud-providers.md#embedded-configuration)。

### 云端验证

继续阅读[云 Provider 设置与限制](cloud-providers.md#cloud-validation)。

## Provider 目录与插件

按 [Provider 目录与插件](provider-plugins.md#provider-catalog-and-plugins)选择或发布可信插件。
