---
title: 智能体、线程与运行
description: 掌握 Service 执行 API：固定智能体版本、幂等提交收件箱消息、批量恢复等待审批的运行以及实时引导活动运行。
---

所有执行 API 均位于 `/api/v1` 下，使用[工作区 API 密钥](http.md#authentication)鉴权（查看需 `read`，运行/引导/中断需 `run`，修改智能体需 `write`）。如需可直接运行的 Shell 示例，请参阅[接入应用](connect-application.md#direct-http-integration)。

- **智能体版本（Agent revision）：** 不可变的版本化配置（`ap_...` + 版本号）。
- **会话与线程（Session & Thread）：** 线程承载单条对话历史；会话归组相关线程（分叉线程与异步子线程）。
- **运行（Run）：** 针对线程收件箱消息的一次已受理执行推进。

## 智能体

在 Console（**Agents → Create manually**）或调用 `POST /api/v1/agents`（填入 `GET /api/v1/models` 中的模型 `key`）创建智能体：

```json
{
  "name": "Support",
  "config": {"model": "your-model-key", "instructions": "Answer product questions precisely."}
}
```

每次修改配置（携带 `If-Match` 调用 `POST /api/v1/agents/{agent_id}/revisions` 传入 `{config, note?, make_default?}`）都会生成新的不可变版本号；未显式锁定版本的后续运行默认使用最新默认版本，已受理运行保持原版本。可通过 `POST /api/v1/agents/validate` 校验草稿。

<span id="set-approvals-and-automatic-review"></span> <span id="configuration-fields"></span> <span id="context-compaction"></span> <span id="tool-permissions"></span> <span id="client-tools-and-questions"></span> <span id="subagents"></span>

- [配置智能体](agent-configuration.md)

## 发起对话

携带唯一的 `Idempotency-Key` 调用 `POST /api/v1/threads` 创建线程（省略 `session_id` 时自动新建会话）并投递首条收件箱消息。保存响应中的 `{thread.id, thread.session_id, entry.id}`，跟进 `entry.assigned_run_id`（见[流式监听与读取结果](stream-and-read-results.md#results)）。

可通过 `/sessions`、`/threads` 与 `/runs` 上的 `?text_query=...` 检索历史对话文本。

## 从 App 发起实时会话

在单进程 `all` 模式的 Service 上开启 `server.realtime_preview`，并为 Agent 选择一个实时模型作为主模型。

1. 从 App 后端用具备工作区 `run` 权限的 API Key 调用 `POST /api/v1/realtime/sessions`。传入 `{"agent_id":"…"}` 开始新会话，或者加上 `thread_id` 继续已有 Thread。仅发送文字、仍接收语音时，可以设置 `audio: false`。
2. 把响应交给客户端。浏览器用 `new WebSocket(url, [subprotocol, ticket])` 建连，`url` 由 Service 地址和返回的 `websocket_path` 拼成 `wss:` URL。本机回环开发可用 `ws:`。票据 30 秒内有效且只能使用一次，长期 API Key 留在 App 后端。
3. 从 `accepted` 帧读取 `session_id`、`thread_id` 和 `run_id`，收到 `ready` 后再发输入。票据建连时无需额外发送启动帧。

默认的 `"transport":"websocket"` 通过 WebSocket 双向传输 24 kHz、单声道、有符号 16 位小端 PCM。

OpenAI Realtime 和 GPT-Live 可以在开启音频输入的情况下指定 `"transport":"webrtc"`。先向 `RTCPeerConnection` 添加静音的麦克风轨道，再通过 WebSocket 发送完整的纯音频 SDP，格式为 `{"type":"offer","sdp":"…"}`。应用 `answer` 帧中的 SDP，等媒体连接就绪且 Service 发出 `ready` 后再打开麦克风。音频在客户端与 OpenAI 之间直传。`GET /api/v1/realtime` 会列出可用传输方式。Grok Voice 使用 `"transport":"websocket"`。

两种传输方式都用 `{"type":"text","text":"你好"}` 发送文字，从 `transcript` 帧读取字幕。收到 `approval_required` 时，把待审批的调用展示给用户，再发送对应的决定 `{"type":"approval","call_id":"…","approve":true}`，拒绝时使用 `approve: false`。挂断时发 `{"type":"stop"}`，Service 保存 Run 后会关闭连接。

## 提交消息

向 `POST /api/v1/threads/{thread_id}/inbox` 发送 `agent_id`、`payload`、可选 `delivery` 与新的 `Idempotency-Key` 继续对话：

- **`delivery: "steer"`（默认）：** 在下一个模型边界并入兼容的活动运行（若线程空闲则直接启动新运行）。
- **`delivery: "next_run"`：** 在收件箱排队，等待当前运行结束后单独启动新运行。

携带线程 `If-Match` 可对 `pending` 消息执行 `PATCH` / `DELETE /api/v1/threads/{thread_id}/inbox/{entry_id}` 或 `PUT .../inbox/order` 调整顺序。通过 `options` 传递单次运行限额或覆盖项：

```json
{"options": {"max_usage": {"requests": 40}, "overrides": {"instructions": "Answer in German."}}}
```

<span id="caller-headers"></span> <span id="command-environment-variables"></span> <span id="retained-tool-executions"></span>

历史导入、调用方请求头与执行策略详见[高级执行选项](execution-options.md)。

### 附加文件

单个 `payload` 支持 1–32 个片段（`text`、已上传的[工作区资产 `asset`](files-and-webhooks.md#assets)、HTTP(S) URL 或 JSON）：

```json
{"content": [{"type": "text", "text": "Summarize this report."}, {"type": "asset", "asset_id": "ast_..."}]}
```

当模型声明对应多模态能力时原生发送媒体，64 KiB 以内文本直接内联，其余文件写入挂载环境 `/workspace/.a13n/attachments/` 并向模型提供路径。

## 等待、审批与提问

当运行需要人工审批（`pending.approvals`）或客户端工具 / `ask_user_question` 回复（`pending.calls`）时，运行会以 `waiting` 状态封存。

```mermaid
sequenceDiagram
    participant App as 你的应用
    participant Service
    App->>Service: POST /threads 或 /inbox
    Service-->>App: Run 1 状态 = waiting (pending.approvals & pending.calls)
    App->>Service: POST /runs/{run_1}/resume (完整批次结果)
    Service-->>App: 继任 Run 2 输出结果
```

### 恢复等待中的运行

携带新的 `Idempotency-Key` 向 `POST /api/v1/runs/{run_id}/resume` 同时提交完整覆盖所有待处理 ID 的 `approvals` 与 `calls`（以及可选补充 `input`），Service 会创建新的继任运行并保留原 `waiting` 记录供审计：

```json
{
  "approvals": {"call_delete": {"action": "deny", "reason": "Keep the file"}},
  "calls": {
    "call_question": {
      "status": "returned",
      "value": {"answers": {"Which color?": "blue"}}
    }
  }
}
```

审批取值为 `{"action": "approve"}` 或 `{"action": "deny", "reason": "..."}`；调用结果为 `{"status": "returned", "value": ...}` 或 `{"status": "failed", "message": "..."}`。

## 中断、分叉与归档

| 目标                       | 端点                                                            |
| -------------------------- | --------------------------------------------------------------- |
| 停止活动或排队中的运行     | `POST /api/v1/runs/{run_id}/interrupt`                          |
| 从已结束的运行分叉出新线程 | `POST /api/v1/runs/{run_id}/fork`（可选 `fresh_environments`）  |
| 永久归档关闭线程           | `POST /api/v1/threads/{thread_id}/archive`（需携带 `If-Match`） |

<span id="follow-a-session-stream"></span> <span id="traces"></span> <span id="find-and-improve-execution-issues"></span> <span id="configure-plugins-in-console"></span>

- [流式监听与读取结果](stream-and-read-results.md)
- [检查运行记录并优化智能体](inspect-runs.md)
