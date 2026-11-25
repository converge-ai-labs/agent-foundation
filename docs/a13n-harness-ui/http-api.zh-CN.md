---
title: HTTP API
description: 通过 Harness UI 的 WebUI HTTP API 与管理 MCP 端点创建线程、提交或实时引导运行、推送收件箱条目、上传附件并监听更新。
---

WebUI 监听服务提供 JSON HTTP API、WebSocket 通道与管理 MCP 端点用于自动化集成。使用实例密钥鉴权（`Authorization: Bearer $HUI_API_KEY`），完整 Schema 可查询 `/api/openapi.json`（或 `/reference/harness-ui-openapi.json`）。

## 通过 MCP 管理项目、会话与收件箱

在支持 Streamable HTTP 的 MCP 客户端中连接 `$HUI_URL/api/automation/mcp`，并携带 `Authorization: Bearer <instance-key>`：

- **项目与会话：** 使用 `list_projects`、`get_project` 与 `create_project` 查找或创建项目；使用 `list_threads`、`get_thread(thread_id)` 查询进度与结果；调用 `create_thread(prompt, project_id?)` 启动新对话；调用 `send_thread_message(thread_id, message)` 在空闲时启动新运行或在运行中实时注入引导（Steering）。
- **推送收件箱条目：** 调用 `post_to_inbox(thread_id, kind, title, message)`（`thread_id` 取已有普通对话、Coordinator 或 Bot 会话 ID，`kind` 取 `result`、`needs_human` 或 `milestone`，`message` 为独立可读的 Markdown），成功返回 `item_id` 并投递到[收件箱](webui.md#read-your-inbox)，不会新增对话轮次或启动运行（受 `inbox.enabled` 控制；见下方[收件箱 HTTP 路由](#inbox)）。

## 创建线程并提交输入

将以下脚本保存为 `conversation.sh`（需设置 `HUI_URL` 和 `HUI_API_KEY`）：

```bash title="conversation.sh"
#!/usr/bin/env bash
set -euo pipefail
: "${HUI_URL:?Set the listener origin}"
: "${HUI_API_KEY:?Set the instance key}"

api() {
  local path=$1
  shift
  curl --silent --show-error --fail-with-body \
    -H "Authorization: Bearer $HUI_API_KEY" \
    "$HUI_URL$path" "$@"
}

api /api/setup | jq .
api /api/selectors | jq .
thread=$(api /api/threads -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}')
thread_id=$(jq -er '.thread_id' <<<"$thread")
printf 'Thread: %s\n' "$thread_id"

receipt=$(api "/api/threads/$thread_id/submit" \
  -H 'Content-Type: application/json' \
  --data '{"parts":["Reply with one short greeting. Do not use tools."]}')
receipt_id=$(jq -er '.receipt_id' <<<"$receipt")
printf 'Receipt: %s\n' "$receipt_id"

while true; do
  operation=$(api "/api/operations/$receipt_id")
  status=$(jq -er '.status' <<<"$operation")
  case "$status" in
    preparing|running) sleep 1 ;;
    completed|suspended|failed|cancelled) break ;;
    *) printf 'Unexpected status: %s\n' "$status" >&2; exit 1 ;;
  esac
done

jq '{status, outcome, failure}' <<<"$operation"
api "/api/threads/$thread_id/transcript" | jq .
if [[ "$status" == suspended ]]; then
  api "/api/threads/$thread_id/decisions" | jq .
fi
```

### 为对话指定项目

从 `GET /api/selectors` 获取 `project_id`，创建线程时传入：

```json
{"title":"Repository conversation","defaults":{"project_id":"project-main"}}
```

提交任务前可通过 `GET /api/threads/{thread_id}/configuration` 确认解析后的工作目录。

### 干预运行中的操作

- **实时引导：** `POST /api/operations/{receipt_id}/steer`，请求体 `{"parts":["Focus on the configuration files."]}`。
- **取消运行：** `POST /api/operations/{receipt_id}/cancel`。
- **工具执行管理：** `GET /api/threads/{thread_id}/tool-executions` 查询状态，`POST /api/threads/{thread_id}/tool-executions/{execution_id}/cancel` 取消驻留执行。

## 单次运行环境覆盖

在 `/submit` 中传入 `environment` 仅覆盖本次运行的环境，不修改线程保存的配置：

```json
{
  "parts": ["Review this project"],
  "environment": {
    "environment_profile_id": "environment-sandbox",
    "local_roots": ["/absolute/path/to/code"],
    "environment_bindings": [],
    "default_environment": "workspace"
  }
}
```

`/submit` 同时支持单次覆盖 `model_id`、`thinking`、`fast` 与 `reasoning_mode`。

### 有序输入体

根 `/submit` 与 `/steer` 在 `parts` 中按顺序接收文本片段与已上传附件 ID：

```json
{
  "parts": [
    "Compare ",
    {"attachment_id": "attachment-first"},
    " with ",
    {"attachment_id": "attachment-second"}
  ]
}
```

先通过 `POST /api/threads/{thread_id}/attachments` 上传附件。

## 常用端点

### 决策、历史与生命周期

| 路由                                            | 用途                                 |
| ----------------------------------------------- | ------------------------------------ |
| `GET /api/threads/{id}/decisions`               | 查询待处理的问题、审批或外部工具请求 |
| `POST /api/threads/{id}/decisions`              | 提交决策回复                         |
| `GET /api/threads/{id}/inputs` / `/transcript`  | 分页读取用户输入与完整对话记录       |
| `DELETE /api/threads/{id}?expected_version=<v>` | 永久删除空闲的根线程                 |

### 编辑可复用资源

通过 `GET/PUT /api/configuration/sources/{path}` 读写 YAML（如 `devices/build.yaml`、`web_providers/research.yaml`），通过 `/api/auth/keys` 管理密钥。

### 修改线程已保存选择

使用 `PATCH /api/threads/{id}/configuration` 或预览并应用项目默认值。

### Bot

通过 `/api/bot/*` 管理常驻 Bot 对话与状态，详见 [Bot](bot.md)。

### 收件箱

通过 `/api/inbox/*` 读取和处理智能体发送的条目，详见[查看收件箱](webui.md#read-your-inbox)。`GET /api/inbox` 返回待处理条目的未读总数 `unread_count` 和各栏的未读数 `unread_by_group`，统计包含所有分页。传入 `{"group":"needs_you"}` 选择需要你处理的事项，或传入 `{"group":"updates"}` 选择结果和里程碑。`POST /api/inbox/mark-read` 清除所选栏的未读标记；`POST /api/inbox/mark-done` 还会将该栏的所有待处理条目移至已完成。两者均返回 `204 No Content`，覆盖所有分页，且不影响其他栏。

### 协调器、MCP 与宿主机工具

- **MCP 与连接器：** 通过 `/api/mcp/*` 与 `/api/connectors/*` 管理驻留 MCP 客户端、响应 [MCP 人工输入](mcp.md#human-input-from-mcp-servers) 与 OAuth 连接器。
- **宿主机文件、Git 与终端：** 开启计算机共享时，可通过 `/api/files/*`、`/api/git/*`、`/api/terminals/*` 操作本地资源。

## 记忆观测

通过 `/api/memory/*` 查看全局与项目记忆文件及自动整理记录。
