---
title: 内置 provider 示例
sidebarTitle: 内置示例
description: 直接使用 Direct Local、Local Envd、Docker 和远程 Envd provider 的可运行示例。
---

可运行的 [`examples/environment-provider`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/environment-provider) 项目直接使用 `a13n-environment`，不依赖 Harness 或模型凭据。云端后端见[云端 Provider](providers.md#cloud-providers)。

## 安装

```bash
cd examples/environment-provider
uv sync --locked
```

外部应用可以安装已发布的 `a13n-environment` 包。

## Direct Local

```bash
uv run environment-provider-example direct_local --workspace /absolute/path/to/workspace
```

Host 创建目录、构建 connector、打开执行对象、读写 `/provider-example.txt`，然后关闭执行对象。目录仍保留。Direct Local 使用 Host 账号，不提供操作系统隔离。

## Local Envd

```bash
uv run environment-provider-example local_envd --executable /absolute/path/to/a13n-envd
```

省略 `--executable` 时，依次检查 `A13N_ENVD_EXECUTABLE` 和 `PATH`。Host 管理 `LocalEnvdProviderRuntime`。每次打开 connector 都创建独立的固定 cwd Session；关闭执行对象只释放该 Session。示例最后关闭 Host 运行时，并保留工作目录。详见[运行 `a13n-envd`](../a13n-envd/index.md)。

## Docker

```bash
# 仓库根目录
make image-sandbox
cd examples/environment-provider
uv run environment-provider-example docker
```

默认镜像为 `a13n-sandbox:local`；可用 `--image IMAGE` 选择其他兼容镜像。Host 通过管理 provider 显式创建容器并保存返回的状态，再据此构建 connector。两次独立打开分别写入和读取文件。关闭执行对象保留容器；Host 最后显式调用 provider 的 `destroy()`。

生产 Host 在打开执行对象前发布管理状态。管理操作在分配目标后失败或取消时，通过 `observed_environment_state(error, fallback)` 取得并发布已观测的引用。执行阶段不写回管理状态。目标停止或缺失时，打开失败，不自动恢复或替换目标。

## 用于 Harness

把 connector 传给可执行 Agent：

```python
result = await executable.run("Inspect the workspace", environment=connector)
```

Harness 为每个挂载打开并关闭独立执行对象。模型需要环境工具时，添加 `DynamicEnvironmentCapability`。完整离线应用见 [Agent 应用示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)。

## HTTP 和 WebSocket Envd

[远程 Envd 指南](remote-envd.md) 介绍已有守护进程和 Host WebSocket 处理器。演示程序显式启动并清理自己管理的守护进程：

```bash
uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd
uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

两者都通过同一个 connector 打开独立 Session。关闭执行对象保留守护进程；只有演示程序的运维代码才停止它。

## 验证

在仓库根目录运行 `make examples-check-all`，检查独立项目的 lint、类型、测试和构建。离线 smoke 路径使用 Direct Local；Envd 和 Docker 需要各自运行时。
