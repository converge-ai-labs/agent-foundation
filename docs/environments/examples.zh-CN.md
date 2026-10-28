---
title: 内置 provider 示例
sidebarTitle: 内置示例
description: 先运行本地文件示例，再了解 Envd Session 和 Docker 目标的所有权。
---

运行 [`examples/environment-provider`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/environment-provider)，了解谁创建目标、谁打开执行对象，以及关闭后保留什么。先用 Direct Local 完成离线文件读写，再通过其他示例引入本地守护进程、受管理的 Docker 容器或远程连接。示例使用 Harness 执行操作，使用 Service 管理 Docker，无需 agent 或模型凭据。云端后端见[云端 Provider](providers.md#cloud-providers)。

## 安装

准备 Python 3.13 或更高版本及 `uv`，从仓库检出目录安装独立示例项目：

```bash
cd examples/environment-provider
uv sync --locked
```

除非另有说明，以下命令均在该目录中运行。项目安装 `a13n-harness` 来执行操作，安装 `a13n-service` 来创建和销毁 Docker 目标。复制完整应用时保留两者；单独执行操作只需 Harness。

## Direct Local

为示例选择工作目录。程序会按需创建目录，并在其中写入 `provider-example.txt`；该文件已存在时会被替换：

```bash
uv run environment-provider-example direct_local --workspace /absolute/path/to/workspace
```

输出应包含 `read: hello from direct_local`、`state: none` 和 `workspace preserved: True`。命令退出后，在工作目录中检查 `provider-example.txt`。

程序创建 connector、打开一个执行对象、读写 `/provider-example.txt`，然后关闭执行对象。文件和目录仍归 Host 所有。Direct Local 使用 Host 账号，不提供操作系统隔离。

## Local Envd

按[运行 Envd](../a13n-envd/index.md)安装或构建原生可执行文件，再传入其绝对路径：

```bash
uv run environment-provider-example local_envd --executable /absolute/path/to/a13n-envd
```

应看到 `read: hello from local_envd`，且工作目录仍保留。添加 `--workspace /absolute/path/to/workspace` 可指定目录；默认值是当前目录下的 `.environment-provider-example/local-envd-workspace`。省略 `--executable` 时，依次检查 `A13N_ENVD_EXECUTABLE` 和 `PATH`。

观察应用中的两层生命周期：`LocalEnvdProviderRuntime` 拥有本地 Device，每次打开 connector 则拥有独立的固定 cwd Session。关闭执行对象释放 Session；示例随后关闭 Host 运行时，工作目录仍保留。

## Docker

启动当前账号可访问的 Docker 守护进程。从仓库根目录构建示例的默认镜像，再运行 Docker 路径：

```bash
make image-sandbox
cd examples/environment-provider
uv run environment-provider-example docker
```

默认镜像为 `a13n-sandbox:local`；可用 `--image IMAGE` 选择其他兼容镜像。应看到相同的 `first read` 和 `re-entry read` 值，最后是 `target destroyed: True`。

按以下步骤追踪示例的生命周期：

1. 通过管理 provider 创建容器，保留返回的状态。
2. 根据状态构建 connector。打开一个执行对象写入文件，再打开另一个读取文件。
3. 关闭各执行对象，保留容器。
4. 显式调用管理 provider 的 `destroy()` 移除目标。

生产 Host 在打开执行对象前发布管理状态。分配失败或取消时，用 `observed_environment_state(error, fallback)` 恢复已观测的引用并发布。执行阶段不会写回管理状态、恢复已停止目标或替换缺失目标。Host 负责的发布流程见[生命周期](lifecycle.md)。

## 用于 Harness

Host 准备好 connector 并构建 Harness executable 后，用 [Harness 环境指南](../a13n-harness/environments.md#supply-a-source)中的 `PreparedSource` 实现包装 connector。以下集成片段假设这些对象已存在：

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

Harness 为每个实际使用的挂载打开并关闭独立 execution。添加 `DynamicEnvironmentCapability` 可向模型提供环境工具。完整离线 agent 流程见 [Agent 应用示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)。

## HTTP 和 WebSocket Envd

准备原生可执行文件后，在示例目录运行任一传输演示。如果二进制文件位于其他位置，请替换路径：

```bash
uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd
uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

每个演示都会启动自己的临时守护进程，通过同一 connector 上的独立 Session 写入和读取，并报告 `independent Sessions: True`。关闭执行对象保留守护进程；演示程序的运维代码随后将其停止并移除临时工作目录。

要连接已有守护进程，或实现自己的 Host WebSocket 处理器，请继续阅读[远程 Envd](remote-envd.md)。

## 验证

使用上面的 Direct Local 命令运行离线 smoke 路径。Envd 和 Docker 示例需要各自的运行时。修改独立示例项目时，在仓库根目录运行 `make examples-check-all`，完成 lint、类型检查、测试和构建。
