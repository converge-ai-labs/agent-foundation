---
title: 高级执行选项
description: 导入历史、传递业务上下文，配置工具交接与执行引导。
---

应用需要导入历史、传递业务上下文或控制工具交接与执行引导时，查阅本页。先完成[应用集成](connect-application.md)，保留已接受的策略，重试前检查不确定的工具结果。

## 导入对话上下文

要继续外部对话，在 `POST /threads` 中将已完成历史放在当前消息旁，并替换 agent 占位符：

```json
{
  "agent_id": "ap_...",
  "message_history": [
    {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "We are planning a trip to Kyoto."}]},
    {"kind": "response", "parts": [{"part_kind": "text", "content": "How many days will you stay?"}]}
  ],
  "payload": {"content": [{"type": "text", "text": "Three days. Suggest an itinerary."}]}
}
```

`message_history` 使用 Pydantic AI 消息 JSON，最多 256 条消息、256 KiB 规范化 JSON。它接受用户文本、模型文本和已闭合的工具调用/结果交换。每个历史调用都必须在下一条响应、用户消息或导入结束前，有名称和 ID 匹配的返回。历史工具作为上下文，不会执行。

指令放在 agent config 中，媒体放在当前 payload 中。导入接受已完成对话内容，不接受系统指令或暂停中的执行。外部应用元数据以及运行/对话 ID 保留在提交 JSON 中供读取，但执行前会清除；导入用量不计入 Service 账目。

导入只初始化这个新线程。后续消息和分叉从已提交检查点继续。导入不会生成历史 Service 运行或显示项，已有线程历史不能替换。已经使用 Pydantic AI 的 Python 应用可通过 `ModelMessagesTypeAdapter.dump_json` 序列化，其他客户端可直接提供 JSON。

## 请求上下文头

通过消息正文传入模型与 MCP 请求共用的业务元数据：

```json
{"options": {"context": {"headers": {"x-trace-id": "trace-123", "x-tenant-id": "acme"}}}}
```

默认开启转发。只放入允许发送给这些 provider 和 MCP 服务器的值。值作为可读运行选项保存；密钥应放在 provider 或连接凭据中。入站 HTTP 头不会自动复制。映射最多 32 个不区分大小写且唯一的名称，值为最多 4096 字符的可打印 ASCII，合计 8 KiB；认证和传输名称属于保留字段。

上下文默认在线程内持久保留。后续消息省略时保留最新已接受的值；显式对象完整替换上下文，`{"options": {"context": {"headers": {}}}}` 则清空。延迟工具或子 agent 结果唤醒的新运行使用线程的最新值，而不是来源运行的旧值。活动运行及其故障恢复尝试保留受理时的快照。Steer 可以省略或重复上下文，不同值会排队等待后续运行。快照线程使用同样的上下文生命周期，同时保留冻结的执行配置。

异步子线程最初继承父运行的快照，随后维护自己的线程级上下文。普通分叉省略时为空；保留快照的分叉继承来源上下文。HTTP 读取提供这些值，MCP 查询投影则移除它们。提示词、历史和命令环境不会自动获得副本。

模型请求头和各连接的 `mcp_headers` 优先于共享同名字段；凭据和受保护协议头始终优先。转发需要同时启用部署类别开关及目标的 `config.forward_context_headers`。省略目标设置时使用 `A13N_HARNESS_FORWARD_CONTEXT_HEADERS`（默认 true）；MCP 保存时写入解析后的值。通过 `A13N_PROVIDERS__FORWARD_MODEL_CONTEXT_HEADERS=false` 或 `A13N_PROVIDERS__FORWARD_MCP_CONTEXT_HEADERS=false` 关闭相应类别。Fetch、浏览器和 Shell 操作不会自动转发这些头。

## 调用方请求头

只有某个 MCP 连接需要非敏感上下文时，使用线程级 `mcp_headers`。在线程创建时设置，或携 `If-Match` PATCH 线程：

```json
{"mcp_headers": {"conn_...": {"x-tenant-id": "acme"}}}
```

将占位符替换为工作空间中已启用的 MCP 连接 ID。最多 32 个连接、合计 16 KiB。认证和协议名称，以及连接凭据自身使用的名称，都会被拒绝。每次运行在接受时固定这些头；分叉和异步子线程继承它们。运行视图不显示这些值。

## 执行策略

分别设置工具交还与引导：

- `tools.mode: inline` 等待工具完成；`auto` 可在前台等待结束后返回保留执行回执。
- `steering: boundary` 在模型边界应用引导；`immediate` 可抢占前台等待。

Service 默认使用 `auto`、五秒前台等待和 `immediate`。以下 agent config 片段显式设置这些值：

```json
{"execution": {"tools": {"mode": "auto", "foreground_wait_seconds": 5}, "steering": "immediate"}}
```

每个字段按**运行覆盖 → 线程覆盖 → 所选 agent 修订版本 → 部署默认值**解析。Agent 使用 `config.execution`，线程创建或 PATCH 使用 `execution`，消息使用 `options.execution`。线程 PATCH 需要 `If-Match`：省略字段保持原值，null 字段恢复继承，`{"execution":null}` 清除全部覆盖。

运行在被接受时固定最终策略，排队消息也是如此。故障恢复、交接、resume 和自动续接保留它。Steer 可以省略或重复匹配字段；修改会返回 `execution_policy_immutable`。需要不同策略时，使用 `delivery: next_run` 提交。即使新运行使用 inline 模式，之前的保留调用仍可访问。

Console 在 agent 配置及线程的**会话操作**中提供**执行策略**。新会话的**选项**和空闲输入框策略控件只影响一次运行；**详情 → 执行策略**显示固定值。前台等待接受 0–300 秒，控制观测等待而非执行截止时间。

### 命令环境变量

在相同三层的 `execution.environment` 中设置非敏感命令默认值：

```json
{"execution": {"environment": {"APP_MODE": "staging", "EMPTY": ""}}}
```

每个变量按**命令 → 运行 → 线程 → agent → 已冻结环境 recipe**解析。线程 PATCH 替换传入的映射；null 或 `{}` 清除该层。空字符串设置空值，删除键则露出下层默认值，并非 unset。显式命令 unset 遵循 provider 支持。Windows 上保持名称拼写一致。

运行固定其 agent/线程/运行三层结果。Shell 命令、后台进程及其普通子进程接收这些值，不修改全局 sandbox 变量。内联子 agent 共享它；分叉和独立子线程使用自己的 agent 与选择。凭据保存在相应密钥存储中。

## 保留的工具执行

自动交还后，支持的调用可在模型收到 `exec-*` 回执后继续执行。读取该引用的结果，不要重新发起调用。保留的 CodeAct 执行包含其嵌套调用。

只要原 daemon 代际和 Envd Session 仍可用，Envd Shell 执行就能在 worker 更换后恢复。新 worker 沿用原执行 ID 观察同一个进程，包括启动响应丢失的情况。恢复使用原始运行捕获的挂载并检查当前授权，不重新启动命令或运行。临时连接失败时继续等待恢复。daemon 代际丢失、Envd Session 过期或原生证据缺失时，执行以 `lost` 结束，副作用仍可能不确定。其他进程内调用，包括挂起的 CodeAct 续执行，在 worker 消失后标为 `lost`。

同一线程跨运行和等待人工输入期间复用兼容的 Envd Session。活跃工作维持保活；最后一次释放后开始空闲窗口，默认为一小时。绑定或执行策略改变时使用独立 Session。线程的执行上下文仍被 worker 持有时，后续运行保持在该 worker 上，直到所有权释放或被接管。

从 Console 会话菜单或运行详情打开**工具执行**。用**检查**查看元数据，用**读取结果**获取有界结果片段。API 位于 `/threads/{thread_id}` 下：

| 端点                                          | 操作                                       |
| --------------------------------------------- | ------------------------------------------ |
| `GET /tool-executions`                        | 列出调用，可按 `run_id` 或 `active` 筛选。 |
| `GET /tool-executions/{execution_id}`         | 查看状态。                                 |
| `GET /tool-executions/{execution_id}/result`  | 使用 `offset`、`limit` 和 `version` 读取。 |
| `POST /tool-executions/{execution_id}/cancel` | 请求取消。                                 |

引用属于确切的一个线程。Agent 获取结果或首次邮箱投递会开始已交付结果的保留窗口，默认一小时，且不超过完成后 24 小时。Console 和 HTTP 检查（包括读取结果）不改变交付状态或保留期。重复读取不延长保留期，过期记录返回 404。

接受取消与完成清理是两件事。停止原始运行会请求取消其保留调用，并禁止迟到结果唤醒对话。停止后续运行影响的是该运行的调用，而非较早调用。保留工作与清理完成前，挂载仍处于使用中。
