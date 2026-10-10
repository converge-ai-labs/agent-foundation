---
title: 生命周期与状态
description: 分别管理环境目标、连接配置和独立执行对象。
---

Host 先管理目标并保存结果，再由其来源向 Harness 返回固定目标的 connector。独立的 `a13n-environment` 包也可直接用于普通应用。

## 三个对象

| 对象                   | 职责                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------ |
| `EnvironmentProvider`  | 账号级管理客户端，显式创建、启动、检查、停止、续期和销毁目标                               |
| `EnvironmentConnector` | 可重复使用的固定目标连接配置；构造时不获取活跃客户端                                       |
| `EnvironmentExecution` | `await connector.open()` 返回的已就绪执行对象，拥有自己的客户端、操作句柄和 `execution_id` |

连接配置可以用于多个 Run。每次 `open()` 都创建独立执行对象。打开、检查就绪和执行清理不会创建、启动、替换或续期目标。已知目标丢失会报错，不能换用同名资源。

`close()` 只释放当前对象拥有的资源。执行关闭后的原生进程是否保留遵循 provider 契约；例如 E2B 断开观测，Direct Local 清理自己启动的进程。目标保留和销毁由 Host 决定。

## 先管理，再发布状态

以下片段中的状态存储、并发控制和取消保护由 Host 提供：

此处 `PreparedSource` 使用 [Harness 环境指南](../a13n-harness/environments.md#supply-a-source)中的 Host 来源实现。它在首次使用时返回已准备好的 connector。

```python
from a13n_environment.docker.provider import DOCKER
from a13n_environment.errors import observed_environment_state

recipe = {"image": "ghcr.io/converge-ai-labs/a13n-sandbox:dev"}
state = await state_store.load(environment_key)
async with await DOCKER.open_provider(configuration=account_configuration) as provider:
    try:
        if state is None:
            state = await provider.create(recipe, environment_id="env-workspace", operation_id="op-create")
        else:
            state = await provider.start(recipe, environment_id="env-workspace", state=state, operation_id="op-start")
    except BaseException as error:
        observed = observed_environment_state(error, state)
        await state_store.publish(environment_key, observed)
        raise
    await state_store.publish(environment_key, state)
    connector = provider.execution_connector(recipe, environment_id="env-workspace", state=state)

result = await executable.run("Continue the task", environment=PreparedSource(connector))
```

`EnvironmentState` 保留原有的无凭据格式。它不包含活跃客户端、执行句柄、Harness 挂载策略或销毁权限。管理方法返回当前引用；`inspect()` 返回状态和引用。

管理部分成功后仍可能失败。`EnvironmentManagementError` 和 `EnvironmentManagementCancelled` 保留当时已知的引用；`observed_environment_state(error, previous)` 也能从异常链中读取它。已确认清空的 `None` 与没有观察到新状态不同。Host 应在释放管理操作归属前，以自己的条件更新和取消保护保存结果。

连接配置的 `state` 是固定的独立副本。执行只验证和使用这个目标，不产生新的管理状态。Run 结束后不需要从执行对象回写目标引用。`HarnessState.environment_states` 是挂载状态汇总，不能取代 Host 的管理记录。

## 显式销毁

```python
async with await definition.open_provider(
    configuration=account_configuration, credential=current_credential
) as provider:
    try:
        await provider.destroy(recipe, environment_id="env-workspace", state=state, operation_id="op-destroy")
    except BaseException as error:
        await state_store.publish(environment_key, observed_environment_state(error, state))
        raise
    await state_store.publish(environment_key, None)
```

只有确认销毁完成才清空状态。不确定的结果保留已知引用，供 Host 后续检查。销毁只针对经过验证的目标及 provider 拥有的材料；共享 Host 目录、绑定源和外部卷不会随执行关闭被删除。

## 管理与执行方法

| 方法                                                                        | 作用                                             |
| --------------------------------------------------------------------------- | ------------------------------------------------ |
| `definition.open_provider(...)`                                             | 获取账号级管理客户端，不选择或改变目标           |
| `provider.create(recipe, ..., operation_id=...)`                            | 显式创建或核对管理目标                           |
| `provider.start(recipe, ..., state=..., operation_id=...)`                  | 显式启动已有目标；不重建丢失目标                 |
| `provider.inspect(recipe, ..., state=...)`                                  | 只读返回 `running`、`stopped` 或 `absent` 及引用 |
| `provider.stop(...)` / `destroy(...)`                                       | 按 provider 能力显式停止或销毁                   |
| `provider.keepalive(...)`                                                   | 显式续期；调度由 Host 负责                       |
| `definition.execution_connector(...)` / `provider.execution_connector(...)` | 纯构造固定目标连接配置                           |
| `connector.open()`                                                          | 打开新的、已就绪的执行对象                       |
| `execution.check_ready(families)`                                           | 只读检查当前执行，不进行目标恢复                 |
| `execution.close()`                                                         | 幂等释放该执行的资源                             |

关闭管理客户端不影响它先前生成的连接配置。借用的运行时仍由 Host 关闭；传入运行时时可以同时提供账号配置，但不能再传凭据。Local Envd 必须借用 Host 运行时；HTTP 和 WebSocket Envd 只提供连接能力，不提供目标管理。

## 处理失败

管理和 provider 边界使用 `EnvironmentProviderError`，操作使用 `EnvironmentError`。通过 `safe_projection()` 向用户展示安全信息，保留完整异常用于本地诊断。

| 确定性           | Host 的处理                            |
| ---------------- | -------------------------------------- |
| `not_dispatched` | 修复输入或运行时条件后，再决定是否尝试 |
| `known`          | 按已知结果处理，不代表一定可以重试     |
| `unknown`        | 先检查原操作和目标，不能直接重放       |

控制面不可用不等于目标不存在。恢复连接需要显式打开新的执行对象；Harness 中使用显式挂载替换。旧执行的进程、输出和电脑操作引用不能用于新执行。

各云环境的停止、文件保留和到期规则见 [Provider 对比](providers.md#reconnection-and-lifecycle)。
