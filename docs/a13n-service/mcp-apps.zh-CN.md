---
title: 嵌入 MCP Apps
description: 在应用中隔离展示交互式 MCP 工具结果，并执行当前权限检查。
---

构建需要展示远程 MCP 交互式结果的应用时，使用本页。准备已验证身份的 Service 客户端、兼容的 MCP 服务器和隔离 iframe 桥接。先[配置并测试连接](tools.md#connections)，再打开保存的 App，不重复原工具调用。

## 供接入方使用的 MCP Apps

接入应用可以根据工具结果渲染 MCP App。Service 保存原始参数、声明和完整结果，包括 `_meta`；应用打开 App 时才获取 HTML。Console 不渲染 App。

实时接入流程：

1. 订阅 `GET /api/v1/sessions/{session_id}/stream`。
2. 将含有 `a13n.mcp_apps` 的 delta 视为加载中的引用。此处即使 `unavailable: false`，也只是临时状态。
3. 等待 `app_ready`，确认原始数据和引用已提交。用 `(run_id, app_id)` 标识每个 App；重连后可能重复收到就绪事件。
4. 使用事件的 `run_id` 和 `apps[].app_id` 打开 App。

包含 App 的检查点提交后，事件跟在已提交的 `items` 后面；无需等待运行结束。发布失败时，引用标记为不可用，不发送就绪事件。通过 `GET /api/v1/runs/{run_id}/items` 加载的可用已提交引用可直接打开，包括断线期间已经结束的运行。Thread 事件流不发送 `app_ready`。

| 请求                                                      | 请求体                                | 结果                                |
| --------------------------------------------------------- | ------------------------------------- | ----------------------------------- |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/open`           | 无                                    | 已保存的原始数据和当前 HTML         |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/tools/call`     | `{"name":"increment","arguments":{}}` | `result` 中的原始 MCP 结果          |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/resources/read` | `{"uri":"ui://counter/app.html"}`     | 已保存 App URI 的当前 HTML 和元数据 |

每次请求使用新的 MCP 客户端并检查当前访问权限。打开 App 不会重跑原始工具；不支持依赖 worker 原 MCP 会话的服务器。按钮调用必须满足原 agent 的工具选择与权限、当前连接的工具选择及 App 可见性。需要审批或模型审核的操作会被拒绝。部署配置了准入策略时，还需要显式支持 App 准入。

接入应用负责创建 iframe、执行资源 CSP 和隔离要求，并通过 MCP App bridge 提供原始参数和结果。应用自行保存视图状态，通过现有运行 API 提交上下文或消息。后续工具结果仅返回调用方，不替换原始结果，也不进入模型历史。调用不会自动重试：收到 `outcome_unknown` 后先检查外部状态，再决定是否重试。HTML 每次重新获取，可能与产生旧结果时的版本不同。升级到 checkpoint format 4 时，应先升级 control 进程和读取端，再启动新 worker。
