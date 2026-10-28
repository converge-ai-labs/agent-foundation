---
title: 生命周期与状态
description: 准备目标、保存引用、跨 Run 复用，并显式销毁目标。
---

要跨 Run 复用工作区，将目标管理与每次执行分开。Host 创建或启动目标并保存引用，Harness 针对该目标打开 execution，并在 Run 结束时关闭 execution。

## 三个对象

| 对象                   | 用途                                                                                |
| ---------------------- | ----------------------------------------------------------------------------------- |
| `EnvironmentProvider`  | 通过 Service 账号级客户端创建、启动、检查、停止、续期或销毁目标                     |
| `EnvironmentConnector` | 描述一个可重复使用的固定目标，构造时不获取活跃客户端                                |
| `EnvironmentExecution` | 在 `await connector.open()` 后使用支持的操作，拥有独立客户端、句柄和 `execution_id` |

跨 Run 复用 connector，为每个使用者分别打开 execution。打开或检查 execution 不会启动、替换或续期目标。已保存的目标丢失时，交回 Host 生命周期管理处理，不选择另一个同名资源。

execution 关闭按后端规则释放资源。Native Docker 和 E2B 断开观测，Direct Local 清理自己拥有的进程，Envd 清理 Session 资源。关闭管理客户端只释放该客户端。两种关闭操作都不销毁目标。

## 先管理，再发布状态

按以下顺序处理托管目标：

1. 按 Host 并发策略认领生命周期操作，并加载当前状态。
2. 尚未选择目标时创建目标，否则启动保留的目标。
3. 发布结果状态，包括发生失败前已观察到的状态。
4. 根据已发布状态构造固定目标的 connector，交给 Run。

下面的异步接入片段需要 `a13n-service`、Docker 账号设置、自有 `state_store` 和 `executable`。`PreparedSource` 是 [Harness 环境指南](../a13n-harness/environments.md#supply-a-source)中的 Host 类。Host 需为加载、管理、发布整段流程提供并发控制，并保证取消时仍可完成状态发布。厂商 I/O 在数据库事务外执行。示例操作 ID 仅作演示；核对操作结果时，沿用实际生命周期操作的身份。

```python
from a13n_service.providers.environments.docker.definition import DOCKER
from a13n_service.providers.environment.errors import observed_environment_state

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
    connector = DOCKER.execution.execution_connector(
        DOCKER.execution_environment(recipe), configuration=account_configuration,
        environment_id="env-workspace", state=state
    )

result = await executable.run("Continue the task", environment=PreparedSource(connector))
```

发布后，Host 记录标识后续要复用的目标。`EnvironmentState` 保存这个不含凭据的引用，不携带活跃客户端、挂载权限或销毁权限。connector 保留独立副本；execution 不产生需要在 Run 后保存的管理状态。`HarnessState.environment_states` 汇总挂载引用，Host 记录仍是权威来源。

创建操作可能已分配目标，随后就绪检查却失败。`EnvironmentManagementError` 和 `EnvironmentManagementCancelled` 保留已观察到的状态。`observed_environment_state(error, previous)` 沿异常链查找该观测；没有新观测时返回先前引用。已确认的 `None` 表示引用已清空。释放生命周期操作认领前，按 Host 的条件更新策略发布观测结果。

## 显式销毁

Host 决定退役目标时，调用管理 provider。以下片段沿用相同的 Host 状态发布和并发控制设置：

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

确认销毁或目标不存在后，清空保存的引用。结果未知时保留已知引用，供后续检查。销毁针对经过验证的资源和 Provider 拥有的材料；Host 目录、绑定源和无关卷仍归原所有者管理。

## 管理与执行方法

按当前阶段选择操作：

| 方法                                                       | 结果或作用                                         |
| ---------------------------------------------------------- | -------------------------------------------------- |
| `definition.open_provider(...)`                            | 获取账号管理客户端，不选择或修改目标               |
| `provider.create(recipe, ..., operation_id=...)`           | 创建目标，或核对同一次分配的结果                   |
| `provider.start(recipe, ..., state=..., operation_id=...)` | 按恢复约定启动引用所指的目标                       |
| `provider.inspect(recipe, ..., state=...)`                 | 只读状态（`running`、`stopped` 或 `absent`）及引用 |
| `provider.stop(...)` / `destroy(...)`                      | 在支持时显式变更生命周期                           |
| `provider.keepalive(...)`                                  | 由 Host 调度续期                                   |
| `definition.execution.execution_connector(...)`            | 已验证的固定目标 connector                         |
| `connector.open()`                                         | 新的已就绪 execution                               |
| `execution.check_ready(families)`                          | 只读检查当前 execution                             |
| `execution.close()`                                        | 幂等清理该 execution                               |

管理客户端关闭后，connector 仍可使用。借用的运行时由独立的 Host 所有者管理：按需提供账号设置，借用时省略凭据，所有借用者结束后再关闭运行时。Local Envd 必须使用这种 Host 运行时。HTTP 和 WebSocket Envd 是仅连接 provider。

## 处理失败

先检查错误，再决定下一步。管理和 Provider 边界使用 `EnvironmentProviderError`，操作使用 `EnvironmentError`。向用户展示 `safe_projection()`，完整异常留作本地诊断。

| 结果确定性       | 下一步                               |
| ---------------- | ------------------------------------ |
| `not_dispatched` | 修正输入或运行时条件，再决定是否尝试 |
| `known`          | 处理已确认结果；另行检查是否支持重试 |
| `unknown`        | 重试前检查或核对原操作和目标         |

控制面失败表示证据不可用，不证明目标不存在。要重新连接，显式打开新的 execution 或替换 Harness 挂载。在新 execution 中使用新的进程、输出和电脑操作引用。

Envd 可通过 [Host 恢复提示](remote-envd.md#state-concurrency-and-recovery)重新附加保留的 Session。该提示独立于可移植目标状态；重新附加既不重放操作，也不恢复旧 execution 引用。停止、文件保留和到期行为见 [Provider 对比](providers.md#reconnection-and-lifecycle)。
