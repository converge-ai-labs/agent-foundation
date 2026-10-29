---
title: 托管浏览器
description: 将共享浏览器绑定到 thread，按需准备，并安全归还人工控制权。
---

托管浏览器在多次 run 之间保留浏览器会话。多个 thread 可以使用同一浏览器及登录状态。Service 独立于[环境](environments.md)管理分配、标签页归属和人工控制。

> [!IMPORTANT]
> 当前版本提供生命周期 API 和原生 agent 工具。内置发行版尚无真实浏览器供应商或 Console 接管界面。部署方需要注册兼容的浏览器 provider，接入应用需要实现控制界面。配置前先查询 `GET /api/v1/provider-types/browser`。

## 预留并绑定浏览器

在同一工作空间内使用[已认证的 HTTP 请求](http.md)。创建、绑定和使用浏览器需要工作空间 `run` 权限；释放需要工作空间 `write` 权限。Provider 账号仍受自身权限约束。

1. 按已注册类型的 schema，通过 `POST /api/v1/browser-providers` 创建浏览器 provider 账号。凭证只通过该 API 保存，不要放入 agent 消息。
2. 通过 `POST /api/v1/browsers` 预留浏览器，设置 `provider_id`、`name` 和 provider 的 `recipe`。省略 `prepare` 时只预留标识，不分配供应商会话。
3. 启用 agent 修订版本的 `browser` 工具集，只选择必要的工具和[权限](agents-and-runs.md#tool-permissions)。
4. 读取 thread 的当前 ETag。向 `PUT /api/v1/threads/{thread_id}/browser` 发送该 `If-Match` 及下方绑定。应在提交需要使用该浏览器的 run 之前完成绑定。

```json
{
  "browser_id": "brw_example",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

将示例 ID 替换为返回的浏览器 ID。通过 `GET /api/v1/threads/{thread_id}/browser` 读取期望绑定；向 PUT 端点发送 JSON `null` 可解除绑定。绑定变更对后续 run 生效，已接受的 run 保留原绑定。子 thread 和 fork 得到的 thread 需要显式绑定。

原生工具支持观察、读取、导航、操作、等待、标签页、对话框、截图及下载记录查看。在 provider 提供经过授权的传输边界之前，上传不可用。浏览器所在主机的文件路径不是 run 所挂载环境中的路径。

## 选择准备和保留策略

| 选项                                                             | 行为                                                                   |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `preparation: "first_use"`                                       | 默认值。首次获准的浏览器调用触发分配；未使用的工具不会分配供应商资源。 |
| `preparation: "run_start"`                                       | 开始执行时准备浏览器。                                                 |
| 创建时设置 `prepare: true`，或调用 `POST /browsers/{id}/prepare` | 在 agent 使用前显式准备。                                              |
| `idle_seconds: 1800`                                             | 默认值。达到可释放的空闲时长后释放；允许 60–604800 秒。                |
| `idle_seconds: null`                                             | 保留到显式释放或供应商会话过期。                                       |

准备与保留策略相互独立。读取浏览器资源的 `status`、`control`、`expires_at` 和 `failure`。`reserved` 表示尚无就绪的分配；`creating` 表示准备结果未确定；`ready` 表示已确认活跃会话。元数据读取不会触发分配，并发准备会收敛到同一分配。

活跃 run、尚未结算的浏览器调用、人工控制和有效的待处理归还通知会阻止空闲释放。供应商到期仍可能终止会话。`lost` 或 `deleted` 的浏览器不会自动重启。需要替换时，创建并绑定另一个浏览器。

Run 完成和解除绑定都会保留浏览器及标签页。要终止会话，先读取浏览器的当前 ETag，再带 `If-Match` 调用 `POST /api/v1/browsers/{id}/release`。活跃使用导致释放不安全时，Service 会拒绝请求。释放不会保存可恢复的 profile，也不会为替换会话保留登录状态。

## 选择标签页访问范围

默认使用 `thread`：原生工具只能看到该 thread 拥有的精确标签页及其弹出页。`global` 暴露实时标签页清单，包括其他 thread 的标签页。两种模式共享 cookie 和登录状态；thread 模式限制工具目标，不隔离账号。

每个 thread 的标签页选择独立于浏览器原生焦点。选中的标签页关闭后，需要显式选择或创建另一个标签页。Service 不会仅凭相同 URL 或标题选择另一页面。Worker 重连恢复精确归属和选择，不恢复旧的可操作引用；操作前应重新观察。

## 接管并归还控制权

接入应用通过直接 HTTP 调用实现下列流程。接管、归还和交互视图访问不进入 MCP 发现。

1. 读取浏览器的当前 ETag，带 `If-Match` 和 `{"thread_id": "<bound-thread-id>"}` 调用 `POST /api/v1/browsers/{id}/takeover`。
2. 查询浏览器，直到 `control` 变为 `human`。`draining` 表示 Service 已关闭浏览器工具准入，正在等待此前获准的工作结束。结果未知的调用不会因超时被当作成功完成。
3. 以控制方主体调用 `POST /api/v1/browsers/{id}/viewer`。返回的 URL 是敏感信息，请遵守有效期和响应的 `no-store` 策略。
4. 完成人工操作后，读取新的 ETag，带 `If-Match` 调用 `POST /api/v1/browsers/{id}/handback`。
5. 等待 `control: "automation"`。Service 会先撤销交互视图输入、同步精确标签页并使旧引用失效，再开放工具。

人工控制期间，该浏览器的所有浏览器工具都会被阻止，包括读取和等待。其他工具、模型工作和其他浏览器继续运行。受阻工具立即返回，并登记其 thread 为归还通知接收方；agent 应继续无关工作或结束，而不是轮询。

归还只向实际发生过受阻调用的 thread 发送内部可用性通知。现有 inbox 和 run 边界决定何时纳入该通知。等待审批的 run 继续等待，失败或取消的 run 保留原有的显式继续规则。停止或归档 thread 会抑制其待处理通知。受阻或结果未知的操作绝不会自动重放。

交互视图过期或 provider 被禁用会撤销视图访问，但不会自动将控制权交还自动化。请显式请求归还。撤销失败时，浏览器工具继续被阻止，直到清理成功。
