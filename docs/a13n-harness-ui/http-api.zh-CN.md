---
title: Harness UI HTTP API
sidebarTitle: HTTP API
description: 通过 HTTP 运行对话、处理决策，并接入配置、文件与实时功能。
---

通过这套 API，你可以用自己的客户端控制正在运行的 Harness UI 实例。先完成一次对话，再按需加入其他控制。监听器与 TUI、[Python 嵌入](embedding.md) 使用同一个 App；Service 的托管 API 则位于独立的 `/api/v1` 下。

| 要构建的功能               | 从这里开始                                                                                                  |
| -------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 发送提示词并读取结果的脚本 | [首次对话](#create-a-thread-and-submit-input)                                                               |
| 处理审批和附件的客户端     | [决策与附件](#metadata-decisions-and-attachments)                                                           |
| 配置编辑器                 | 先[查看](#inspect-configuration-and-working-state)，再[保存](#configure-projects-and-threads)               |
| 流式对话界面               | 先读取[历史](#conversation-input-navigation)，再接入[实时通道](#observe-realtime-channels-and-recover-gaps) |
| 协作编辑器或原生工作台     | [共享输入框](#shared-composer)、[Files](#native-host-files) 或 [Terminal](#native-terminal)                 |

## 认证与查看协议约定

1. 完成[初始化](setup.md)，确认选中的 Agent 能回答提示词。

2. 启动监听器。如果只接入对话，保持原生 Host 面板关闭：

   ```bash
   a13n-harness-ui webui --no-share-computer
   ```

3. 保持进程运行。在另一个 Bash 终端中设置输出的监听地址，并输入实例密钥，避免将密钥写入 shell 历史：

   ```bash
   export HUI_URL=http://127.0.0.1:8000
   read -r -s -p 'Harness UI instance key: ' HUI_API_KEY
   printf '\n'
   export HUI_API_KEY
   ```

   将示例地址替换为实际监听器 URL。这里使用实例密钥，不是模型 provider 凭据。

4. 检查访问并获取当前运行版本的协议约定：

   ```bash
   curl --fail-with-body "$HUI_URL/api/status" \
     -H "Authorization: Bearer $HUI_API_KEY"
   curl --fail-with-body "$HUI_URL/api/openapi.json" \
     -H "Authorization: Bearer $HUI_API_KEY"
   ```

成功的状态响应包含 `api_version: "1"`、构建信息、App 状态、访问模式和功能标志。精确字段与约束以运行实例的 OpenAPI 为准。[生成的 schema](/reference/harness-ui-openapi.json) 描述本源码版本；Swagger 和 ReDoc 页面未启用。

所有者 API 请求使用 `Authorization` 请求头。监听器还校验 `Host` 和同源 `Origin`。不要把密钥放进查询参数或日志。`--dangerous-skip-permissions` 会关闭监听器认证，不能用来解决认证配置问题。实例密钥客户端共享工作台权限；受限访客访问见[分享](#subscription-accounts-and-sharing)。

## 创建 Thread 并提交输入

将以下代码保存为 `conversation.sh`。它需要 Bash、curl、jq、上面两个环境变量，以及可用的默认配置。脚本创建一个 Thread，只提交一次，轮询回执，然后读取保存的历史。提示词仍按所选 Agent 的实际权限运行。

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
  --data '{"parts":["Explain this project without changing files."]}')
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

运行 `bash conversation.sh`。回执表示请求已获准执行。`preparing` 和 `running` 表示工作仍在进行。进入终态后，分别检查以下结果：

| 字段                   | 检查内容                                  |
| ---------------------- | ----------------------------------------- |
| `outcome.execution`    | 执行状态、输出、输出省略标志和失败信息    |
| `outcome.continuation` | 是否成功选择了下一份 continuation         |
| `outcome.environment`  | Environment 发布与清理结果                |
| `failure`              | 操作级失败，包括尚未产生 outcome 时的失败 |

要继续对话，向同一个 Thread 提交另一份 `parts` 请求体，并轮询新回执。根操作没有 HTTP wait 端点。每个 Thread 同时只允许一个活跃根操作；第二次提交会被拒绝，不会排队。

客户端应保留两个 ID。提交响应不确定时，先检查 `GET /api/threads/{thread_id}` 及其根活动，再决定下一步。提交没有持久幂等保证。回执属于 App 进程，重启后可能消失；已保存的对话状态仍可读取。停止轮询脚本只会停止观察，不会停止服务器上的 Run。

### 调整运行中的操作

向 `POST /api/operations/{receipt_id}/steer` 发送 `{"parts":["Focus on the configuration files."]}`，或通过 `POST /api/operations/{receipt_id}/cancel` 请求取消。随后继续轮询同一操作。控制请求被接受，不代表取消或回滚已经完成。

根级和子级 steering 都使用全局 `input.steering_mode`：默认为 `immediate`，设为 `queued` 则等待下一个安全边界。没有逐条消息的模式。支持保留的工具调用可以跨即时 steering 继续。通过 `GET /api/threads/{thread_id}/tool-executions?offset=0&limit=20` 查看指定 Thread 的状态（`limit` 为 1–100），通过 `POST /api/threads/{thread_id}/tool-executions/{execution_id}/cancel` 取消一项。这些进程内状态记录既不暴露也不消耗私有结果。见[工具执行控制](webui.md#tool-executions-and-immediate-steering)。

## 元数据、决策与附件

### 回答暂停操作的决策

1. 读取 `GET /api/threads/{thread_id}/decisions`。非空批次提供 `continuation_id`、`requests`，以及适用的交互截止时间。
2. 收集当前批次的答案。逐一匹配 `request_id` 和 `kind`，按问题选项或外部结果约定填写。
3. 向同一路由 POST `DecisionResponseBatch`。将当前 continuation ID 填入 `expected_continuation_id`。
4. 轮询返回的**新回执**。如果 continuation 已过期而发生冲突，先重新读取决策，再询问或提交下一份答案。

只有一项审批时，请求体如下。将两个占位符替换为读取到的值，并明确决定是否批准：

```json
{
  "expected_continuation_id": "<current continuation ID>",
  "responses": [
    {"kind": "approval", "request_id": "<pending request ID>", "approved": true}
  ]
}
```

问题响应使用 `kind: "question"`，附带 `answers` 和/或自由文本 `response`。外部结果使用 `kind: "external"` 和 `result`；拒绝时使用 `denied: true` 与 `denial_message`。ID 必须唯一且属于选中的待处理批次。API 允许漏项：未回答的审批会被拒绝，未回答的外部请求（包括问题）会失败。普通提示词也会按这些未答请求规则继续执行。恢复前应收齐打算提交的答案。

### 上传附件

先暂存原始字节，再提交引用。用 `name` 查询参数命名，不使用 multipart 表单：

```bash
curl --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/attachments?name=notes.txt" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @notes.txt
```

将 `THREAD_ID` 设为已有 Thread 的 ID。保留返回的 `attachment_id`，用于[有序输入体](#ordered-input-bodies)。每个文件最多 10 MiB，每次输入最多八次附件引用，总计 20 MiB。同一个附件 ID 重复出现会重复计数。元数据通过 `/attachments/{attachment_id}/metadata` 读取；下载通过 `/attachments/{attachment_id}` 返回带 attachment disposition 的字节。ID 归属于暂存它的 Thread。

### 重命名或归档 Thread

向 `/api/threads/{thread_id}/metadata` PATCH `expected_version` 和 `patch`，其中版本取当前 `metadata_version`。省略标题表示不改，null 表示清空。`archived` 必须为布尔值，且归档要求根操作不活跃。元数据版本、配置版本和 continuation ID 分别保护不同操作。

## 仅本次 Run 的执行环境

当一次 Run 需要不同的执行选择时，在 `/submit` 中加入 `environment`：

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

从 `/api/selectors` 选择 profile ID：`environment-native` 是 Full Control，`environment-sandbox` 是 Sandbox。省略的 Environment 选择轴继承 Thread 已保存的选择，空集合清空选择；null 只可清空 `default_environment`。补丁不改变已保存配置及其版本。Environment 准备可能在请求获准后失败，应检查回执，而不是假定环境就绪或会回退到 Full Control。

`/submit` 还接受仅本次 Run 生效的 `model_id`、`thinking`、`fast` 和 `reasoning_mode`。省略或 null 控制值继承 Model 设置；`thinking: false` 请求 Off。Selectors 提供支持的值及禁用原因。无效选择会被拒绝。Steering 不接受 Environment 或 Model 覆盖。决策响应保留暂停 continuation 捕获的 Environment；以后普通提交重新继承已保存的默认值。

## 对话输入导航

通过 `/api/threads/{thread_id}/inputs` 读取普通输入轮次的轻量目录，其中不包含 steering 和隐藏输入。每项都有输入标识、预览、输入位置、不包含在范围内的结束位置，以及可选的已保存最终响应位置。沿 `next_cursor` 分页；`limit` 为 1–100。

通过 `/api/threads/{thread_id}/transcript` 读取消息。首次请求提供 `turn_id` 可定位某个轮次。`next_cursor` 向前读取较早内容，`newer_cursor` 向后读取较新内容。游标和 `expected_continuation_id` 绑定观察到的历史。发生不匹配后应刷新，避免混用不同 continuation 的消息位置。

Transcript 分页附带相交的 `turns` 和 `boundary_entries`，用于显示普通分页范围之外的输入或最终答案。按位置去重，并分页补齐中间缺失的条目。最终位置来自已保存的执行结果，不由最后一条 assistant 文本或实时文本结束事件推断。

## 已保存输出

对子执行，GET `/api/threads/{thread_id}/children/{execution_id}/saved-output` 可获得类型化目标和有界文本窗口。向 `/api/threads/{thread_id}/saved-output` POST 一个目标，可通过可选的 `offset` 和 `limit` 查询参数读取其他窗口。

根目标标识产出它的 Thread、continuation 摘要值及原始消息/part 位置。目标过期时重新获取。这些读取接口暴露已保存的输出，不保留更早的 checkpoint。

## 查看配置与工作状态

按问题选择读取接口：

| 问题                         | 路由与解释                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------- |
| 可以选择哪些资源？           | `GET /api/selectors`；`/api/catalog` 则列出已发现的实现                                            |
| 新 Thread 会使用什么？       | 向 `POST /api/threads/configuration-preview` 提交 `NewThreadDefaults`；查看各选择轴的 `provenance` |
| 这个 Thread 下次会使用什么？ | `GET /api/threads/{thread_id}/configuration`；查看 `next_run`                                      |
| 已获准的 Run 捕获了什么？    | 同一响应的 `captured`、`capture_source`，以及精确 Run/回执或 continuation ID                       |
| 哪些工具来源被分组？         | Thread 配置显示所选来源；`/api/agents/{agent_id}/tool-search` 显示 Agent 默认值                    |
| 上次模型请求多大？           | `/api/threads/{thread_id}/context-usage`                                                           |
| 观察到了多少用量？           | `/api/threads/{thread_id}/usage`；分别提供根、后代和合计用量                                       |
| 有哪些任务或笔记？           | `/api/threads/{thread_id}/work?include=notes` 或 `include=tasks`                                   |

创建时的来源为 `explicit`、`project`、`agent`、`global` 或 `builtin`。已有的保存选择报告为 `thread`；即使与当前默认值相同，也不能据此还原最初来源。这些读取只检查配置，不连接 MCP，也不证明 provider 已就绪。

工作活跃时，`captured` 为 null 表示该操作尚未发布组合配置。没有活跃工作时，`selected_continuation` 读取保存的组合配置。之后的配置编辑不改变此前的捕获。捕获内容省略指令、凭据、transport/配置载荷及依赖导入；`omitted_fields` 列出这些类别。直属子级摘要最多 100 项，并附带省略数量。

## 配置 Project 与 Thread

### 编辑可复用资源

1. GET `/api/configuration/sources`，再读取 `/api/configuration/sources/{relative_path}`，检查已接受的文件。已接受代际可能不同于磁盘上的无效编辑。MCP 源内容可能含凭据，因此返回 null；不要把这个 null 写回去。
2. 向 `/api/configuration/validate?path=projects/work.yaml` POST `{"content":"..."}`，校验修改后的完整配置。
3. 向 `/api/configuration/sources/projects/work.yaml` PUT 同一请求体以发布。对可移除的非根源文件使用 DELETE。
4. 检查发布响应和已接受代际。校验不预留发布资格；源文件保存采用最后写入生效，而不是 expected-digest 更新。

替换可以修复无效文件，删除可以移除无效且未使用的文件。其他无效源仍会阻止发布。删除根文件、路径穿越、symlink 源和任意 Host 路径都会被拒绝。账户凭据另行存储。传输结果不确定时，先读取当前源再重试。

### 修改 Thread 保存的选择

向 `/api/threads/configuration-preview` 直接 POST `NewThreadDefaults` 可预览创建；实际创建 Thread 时，将这些默认值放在 `defaults` 下。例如 `{"project_id":null}` 会禁用全局 Project 默认值。预览不创建对象，创建时会重新解析配置。

对于已有 Thread，向 `/api/threads/{thread_id}/configuration` PATCH `expected_version` 和 `patch`。支持 `project_id`、`agent_id`、`default_model_id`、`local_roots`、`environment_profile_id`、`environment_bindings`、`default_environment`、`harness_plugin_ids`、`environment_run_extension_ids` 和 `mcp_server_ids`。省略表示保留，空列表表示不选择任何项。切换 Agent 不改变其他已保存选择轴。活跃捕获和子 Thread 都保持不变。

要采用所选 Project 的默认值：

1. GET `/api/threads/{thread_id}/project-defaults`，检查 `current`、`replacement` 和 `patch`。
2. 将返回的 `expected_version` 和 `defaults_digest` POST 到该路由。
3. 收到 409 时刷新预览，再接受变化后的默认值。

只应用 Project 指定的选择轴。如果只想更新四个 Environment 选择轴，使用 GET/POST `/api/threads/{thread_id}/project-environments`；其预览描述完整 Environment 替换。两个操作都不复制文件。

## Coordinator

通过 `POST /api/threads` 和 `{"defaults":{"project_id":"project-main"},"coordinator":true}` 创建所有者。使用可用的 Project ID。创建会保存角色并启用自动后续处理；输入另行提交。提交失败不会删除已创建的 Thread。

通过 `POST /api/threads/{thread_id}/coordinator` 将已有普通根提升为 Coordinator。该 Thread 必须属于已接受的 Project、未归档、无活跃操作且没有待处理决策。提升保留身份、历史、标题和设置；对符合条件的 Coordinator 重复调用是幂等的。Worker 和子级不能提升，也没有降级或收养 worker 的 API。

向该路由 PATCH `{"auto_followup":false}` 可暂停自动生命周期通知，true 则启用。这一设置采用最后写入生效，与 Sidekick 配置独立。Coordinator 和 worker 永久保留所属 Project。恢复已归档 Coordinator 使用普通的带版本元数据 API。

通过 `POST /api/threads` 创建 worker，指定 `coordinator_thread_id`，并在 `defaults` 中选择所有者所属的可用 Project。所有者必须未归档。所有权永久保存，且与 `coordinator:true` 互斥；人工创建使用提供的默认值，不应用 Sidekick。创建不启动 Run。启用后续处理时，首次人工提交会安排尽力发送的所有者通知。客户端提供 `thread_id` 后，可以核对结果不确定的创建，而不另建 worker。

从 Thread 摘要读取 `role`（`ordinary`、`coordinator` 或 `worker`）、`coordinator_thread_id` 和 `auto_followup`。用 `coordinator_thread_id` 筛选 worker，或以 `independent_only=true` 排除 worker。游标绑定这些过滤条件。未过滤的人工搜索包含所有根；一个 Project 可以有多个 Coordinator。旧 Project lead 绑定迁移一次并保留 ID/历史；禁用的绑定转为关闭后续处理的 Coordinator。

## 导航与查看子级工作

| 需求                 | 请求                                                                                     |
| -------------------- | ---------------------------------------------------------------------------------------- |
| 导航活动和待处理数量 | `GET /api/threads/activity`，附带 Project/查询/归档/游标/limit 过滤条件                  |
| 已知根的摘要         | `POST /api/threads/lookup`，请求体 `{"thread_ids":["<thread ID>"]}`；1–100 个 ID         |
| 精确父级的子级       | `GET /api/threads/{thread_id}/children`，可选 `execution_id` 或 cursor/limit             |
| 调整子级输入         | `POST /api/threads/{thread_id}/children/{execution_id}/steer`，请求体 `{"prompt":"..."}` |
| 取消子级             | `POST /api/threads/{thread_id}/children/{execution_id}/cancel`                           |

Lookup 包含已归档根和引用不可用 Project 的根，省略不存在的 ID 与子级 ID，并合并重复项。它不分页，也不加载 continuation 状态。子级控制只确认请求，之后仍需查看子级。与根 steering 不同，子 steering 仅接受文本，拒绝附件字段。

根摘要可能包含最近一次成功保存的根 Run 标记 `completion: {version, run_id, continuation_id, completed_at}`。该标记跨重启保留，后续失败或 checkpoint 不会清除它。Transcript 的 `completion_version` 标识实际读取的快照。实现个人已读状态的客户端应确认已渲染的版本，而不是更新的摘要版本；服务器没有逐用户已读确认端点。

## MCP 输入与集成控制

MCP 人工输入属于执行中的工具调用，不走上面的暂停决策流程：

| 请求                                                               | 操作                               |
| ------------------------------------------------------------------ | ---------------------------------- |
| `GET /api/threads/{thread_id}/mcp/integrations`                    | 查看保留的代际，不建立连接         |
| `POST /api/threads/{thread_id}/mcp/integrations/{server_id}/close` | 关闭该所属 Thread 的代际；返回 204 |
| `GET /api/threads/{thread_id}/mcp/inputs`                          | 读取该 Thread 及后代子级的请求     |
| `POST /api/threads/{thread_id}/mcp/inputs/{request_id}/response`   | 回答精确的活跃请求，不启动 Run     |

检查模式和原始 schema。表单接受 `{"action":"accept","content":{"name":"Ada"}}`；URL 确认使用 `{"action":"accept"}`。拒绝或取消时使用相应 `action`。答案被接受后，继续观察原操作。

相同答案可核对重试，冲突答案会失败。传输结果不确定时，重新读取并只重试完全相同的答案，不要重试业务工具调用。已过期、退役或不可用的请求不能授权另一次发送。请求仅存在于当前进程，重启即消失。见 [MCP 人工输入](mcp.md#human-input-from-mcp-servers)。

## Skill 目录与引用

向 `POST /api/threads/skills-preview` 提交 `NewThreadDefaults` 可预览新 Thread 的 Skills。对于已有 Thread，GET `/api/threads/{thread_id}/skills`；也可向同一路由 POST `EnvironmentSelectionPatch`，预览仅本次 Run 使用的 roots。空闲 Thread 显示下次 Run 的目录，活跃 Run 保留已固定目录。预览不修改 Thread。

将结果的 `catalog_id` 和条目的 `item_id`/`name` 放入 submit 或根 steer 的可选 `skill_references` 数组（最多 512 项）。文本中保留 `$name`。适用目录的引用必须匹配条目标识；旧目录按名称解析。缺失、歧义或重复引用会拒绝输入。引用选择指导内容，不展开其字节，也不授予权限。

## Memory 观察

GET `/api/threads?memory=true` 列出 Memory Thread，普通列表排除它们。`projectless=true` 选择 Global memory，Project 过滤保留正常分页。`/api/threads/lookup` 接受同样的 `memory` 区分字段。Memory Thread 暴露 `memory_scope`，并支持 transcript、live、configuration 和 usage 读取。执行或修改返回 `memory_thread_read_only`。

GET `/api/memory/files` 列出 Global 文件，`/api/memory/file?path=MEMORY.md` 读取一个文件。添加 `project_id` 可选择已配置 Project 的作用域。这些经过认证的只读路由独立于 computer sharing，并要求启用 Memory。它们暴露作用域内相对文件，不触发整理。

## 订阅账户与分享

账户初始化从 [Model 与认证](models-and-authentication.md) 开始。以下 API 组分别负责不同操作：

| 目的                    | 路由                                                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 查看/选择 provider 账户 | `GET /api/auth/accounts/{provider}`，GET `/sources`，PUT `/selection`，请求体 `{source, account_id}`                      |
| 发现账户专属模型        | `POST /api/auth/accounts/{provider}/models`                                                                               |
| 管理 provider 密钥      | GET/PUT `/api/auth/keys`，DELETE `/api/auth/keys/{reference}`                                                             |
| 完成登录流程            | POST `/api/auth/logins`，GET/DELETE `/api/auth/logins/{session_id}`                                                       |
| 管理原生账户身份        | GET `/api/subscriptions`，POST `/api/subscriptions/accounts`，GET/PATCH/DELETE `/api/subscriptions/accounts/{account_id}` |
| 刷新/退出/兑换重置      | POST 账户 `/refresh`、`/logout` 或 `/reset`；GET `/reset-vouchers`                                                        |
| 查看所选 Copilot 额度   | `GET /api/subscriptions/copilot/usage`                                                                                    |

Provider 查看支持 `codex`、`grok`、`copilot` 和 `chatgpt`。来源选择支持 Copilot 账户与 ChatGPT 原生注册项，不接受客户端文件路径。DELETE `/api/auth/accounts/{provider}` 退出本地凭据，与取消登录会话不同。兑换重置是显式操作，与刷新用量分开。

使用实例凭据管理分享授权：GET `/api/shares` 按 `scope`/`target_id` 查询，POST 创建，PATCH `/api/shares/{share_id}` 修改权限，DELETE 撤销。创建只返回一次密钥；响应不确定时先检查已有授权。访客使用受限分享界面，不能管理链接或实例设置。权限和到期规则见 [WebUI](webui.md)。

## Device 连接与工作环境

通过配置源创建可复用 Device 连接。例如，向 `/api/configuration/sources/devices/build.yaml` PUT [Device YAML](environments-and-projects.md#add-device-bindings)。Device 凭据与监听器密钥分开。

| 请求                                       | 结果                                         |
| ------------------------------------------ | -------------------------------------------- |
| `GET /api/devices`                         | 已配置的 ID、名称和 carrier 类型；不尝试连接 |
| `GET /api/devices/{device_id}`             | 可用性、路径风格、工作目录与发现支持         |
| `GET /api/devices/{device_id}/directories` | 读取一页目录，不创建 Envd Session            |
| `WS /api/devices/{device_id}/connect`      | 原生 daemon 反向接入                         |

目录查询接受 `path`、`offset`（0–1,000,000）和 `limit`（1–200，默认 100）。省略 `path` 使用 Device 默认工作目录；沿 `next_offset` 分页。路径属于 Device。在线 Device 也可以禁用发现，一个目录不可读不代表离线。这些读取既不创建 Thread，也不创建 Envd Session，且不要求 computer sharing。

Daemon 反向连接协商 `eip.v1`，在 upgrade 请求头中使用 Device Bearer 认证。它不使用浏览器首帧认证或 URL 凭据。

在 Thread 创建的 `defaults` 或配置补丁中设置绑定：

```json
{
  "project_id": null,
  "environment_bindings": [
    {"device_id": "device-build", "alias": "build", "working_directory": "/work/repository"}
  ],
  "default_environment": "build"
}
```

离线时也能保存已知绝对目录。每次 Run 捕获选择，并准备新的执行专属 Session。`default_environment` 用于路由相对路径和省略的 cwd，不限制访问范围。原生 Files、Changes 和 Terminal 始终操作监听器 Host。

### 移除离线配置并修复选择

1. 通过源更新，移除当前 Project/全局默认值中的引用。

2. DELETE Device 源或自定义 Environment profile。这只移除本地配置，不联系 Device，也不删除远程文件。内置 profile 不是可移除文件。

3. 检查已有 Thread 配置，并用当前版本显式修复下次选择：

   ```json
   {
     "expected_version": 1,
     "patch": {"environment_bindings": [], "default_environment": "thread-files"}
   }
   ```

选择了本地 roots 时，`workspace` 也是有效默认值。移除所选默认挂载而不提供替代值会失败。删除不会级联修改 Thread 选择、活跃捕获或已保存历史。

## 原生 Host Files

启用 computer sharing 后才能使用这些端点；`webui` 默认启用，而上面的教程将其关闭。Files 使用服务器 OS 账户的权限，包括 Docker 中的容器挂载。Project roots 是导航起点，不是访问边界。关闭共享时，在访问文件前返回 `403 host_files_disabled`。

### 浏览与编辑文件

1. GET `/api/host/files?path=<absolute-path>` 读取目录。默认每页 200 项，最多 500 项；扫描超过 10,000 项会被拒绝。携带上一页的 `directory.revision` 沿 `next_offset` 继续；冲突后重新开始列目录。
2. GET `/api/host/files/metadata?path=...` 读取条目自身信息，或 `/api/host/files/info?path=...` 读取解析后的普通文件元数据和按文件名推断的 MIME。后者跟随 symlink，并接受 `expected_revision`。
3. GET `/api/host/files/text?path=...`。`presentation:text` 提供最多 512 KiB、不含 NUL 的完整 UTF-8；`binary` 和 `too_large` 不是可编辑文本。
4. 向 `/api/host/files/text` PUT `path`、编辑后的 `text` 和观察到的 `expected_revision`。省略 revision 表示仅创建。收到 `409 host_files_conflict` 时，保留编辑并重新加载以比较。

保存前显式选择 symlink 解析后的目标。原子替换硬链接文件只改变所选条目。原始上传使用 PUT `/api/host/files/content?path=...&expected_revision=...`、octet-stream 字节及 10 MiB 上限；仅新文件可省略 revision。

### 下载或预览字节

向 `/api/host/files/transfers` POST 已查看的文件 revision：

```json
{"path":"/absolute/clip.mp4","expected_revision":"<reviewed revision>","disposition":"inline"}
```

在浏览器中直接使用返回的 `url`。它授权 GET/HEAD `/api/host/files/transfer` 访问指定 revision 和 disposition，30 分钟后过期，不含实例密钥。监听器重启会使其失效。默认 `attachment` 强制 octet-stream 下载。`inline` 允许推断为图片（SVG 除外）、音频和视频的类型；其他类型转为下载。浏览器解码失败后仍可下载原文件。

传输按最多 256 KiB 分块，没有整文件大小上限。单个字节范围返回 206，无法满足的范围返回 416。HEAD 没有响应体。不支持的或多范围请求被忽略，If-Range 不匹配返回完整内容。大小不可靠的文件读取到 EOF，不使用 Content-Length 或 ranges。Revision 变化会在响应头前返回 409，或终止已开始的传输。申请新链接前刷新元数据。独立的图片预览解码上限为 10 MiB。

### 创建、移动或删除条目

| 操作                               | 请求体与行为                                                                         |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| POST `/api/host/files/directories` | `path`；父目录必须存在                                                               |
| POST `/api/host/files/move`        | `path`、`destination`、`expected_revision`；目标必须不存在；不回退为跨设备复制再删除 |
| POST `/api/host/files/delete`      | `path`、`expected_revision`、可选 `recursive:true`；否则目录必须为空                 |

递归删除最多处理 10,000 项和 128 层。Symlink 按条目删除。不支持的不覆盖移动返回 `host_files_unsupported`；部分删除返回 `host_files_partial_failure` 和已完成的移除。再次执行破坏性操作前先刷新。

Revision 是不透明的 OS 元数据观察值，不是内容哈希或历史版本。读写都会检查 revision，但外部进程仍可能与原生修改竞争。请求中断时，文件系统操作可能已经完成。重试前读取当前状态；修改不会自动重放。

### 选择已查看的文件内容作为输入

向 `/api/threads/{thread_id}/host-file-captures` POST `path`、已查看的 `expected_revision`，以及可选且成对的一基、闭区间 `start_line`/`end_line`。接口在选择时读取文件，返回带不可变来源元数据的 `attachment`；不超过 64 KiB 的捕获文本还会返回 `prompt_text`。

通过 `parts` 提交附件 ID。小段捕获文本附带来源以内联方式传入；二进制或较大捕获保留为可读取的 Thread 附件。之后的 Host 编辑不改变这些字节。根 steering 使用相同准备流程；子 steering 仍仅接受文本。

## 原生 Git Changes

通过这些只读路由，选择已查看的 diff 作为输入。它们操作监听器 checkout，并要求 computer sharing。缺少 Git 返回 `503 host_git_unavailable`，关闭共享返回 `403 host_git_disabled`。

1. GET `/api/host/git/repository?path=<absolute-native-path>`。检查 `state`（`repository`、`not_repository` 或 `bare`）和仓库标识：`root`、`git_dir`、`common_dir`、`head_oid`、`branch`。Detached/unborn 仓库分别可能没有 branch/HEAD。
2. GET `/api/host/git/status?path=...&include_ignored=false`。读取分开的 index/worktree 状态及仓库相对路径。默认每页 200 项，最多 500 项；扫描超过 10,000 项会被拒绝。后续页使用 `offset` 和 `expected_revision`；该 revision 覆盖状态记录，不覆盖每个文件的字节。
3. GET `/api/host/git/diff?repository_path=...&path=...&comparison=unstaged`。`staged` 比较 HEAD 与 index，`unstaged` 比较 index 与 worktree，`untracked` 用于新文件。路径按字面值处理。检查 `text`、`binary` 或 `unchanged` 展示形式及 diff revision。
4. 向 `/api/threads/{thread_id}/host-git-captures` POST `repository_path`、相对 `path`、`comparison`、`expected_revision`，以及可选的一基闭区间 **patch** 行号。服务器在暂存附件前重新计算已查看的 diff。Binary/unchanged 捕获会被拒绝；二进制字节使用 Files。

通过 submit 或根 steer 发送返回的附件 ID。已保存来源保留仓库、路径、比较方式、HEAD/index/diff revisions 和选择范围。不超过 64 KiB 的捕获作为带来源的内联文本；更大的 patch 保留为附件。

编辑后或返回 Changes 时按需刷新。Git 状态属于共享 checkout，不表示某次 Run 的独立改动。子进程限制为 15 秒、2 MiB stdout 和 64 KiB stderr；超量/超时返回 413/504。API 不会把部分 patch 当作完整结果，禁用 external diff/textconv helpers，也不执行暂存、commit、discard、分支或远程操作。

## 原生终端

创建 PTY 前检查 `features.host_terminal`。Linux/macOS 在 shell 可用且启用 computer sharing 时支持原生交互终端。Windows 报告 `host_terminal_unavailable`；关闭共享报告 `host_terminal_disabled`。

1. 向 `/api/host/terminals` POST `{"cwd":"/work","rows":24,"columns":80}`，可选一个已接受的 `project_id`。保留返回的 `terminal_id`。初始 cwd 属于 Host，不随导航变化。

2. 通过同源 WebSocket 连接 `/api/host/terminals/{terminal_id}/connect?cursor=<last-end>`。十秒内发送首个文本帧 `{"api_key":"<instance key>"}`；bypass 监听器则发送 `{}`。无效凭据在资源查找前以 4401 关闭。

3. 读取 `TerminalFrame`，获得连接专属 participant、终端视图和 base64 字节。保留流式 UTF-8 解码器，将 `end` 保存为重连游标。最多保留 1 MiB；`gap:true` 表示缺少字节或游标超前。

4. 使用最新 `control_epoch` 申请控制权，再用新 epoch 输入或调整尺寸：

   ```json
   {"kind":"control","control_epoch":0}
   ```

   成功申请返回 epoch 1 后：

   ```json
   {"kind":"input","control_epoch":1,"text":"pwd\n"}
   {"kind":"resize","control_epoch":1,"rows":40,"columns":120}
   ```

5. 用 `{"kind":"control","control_epoch":1,"release":true}` 释放，或 DELETE `/api/host/terminals/{terminal_id}` 关闭共享会话。

每个连接最初都是查看者。申请/接管会增加 epoch；过期命令返回 `host_terminal_control_conflict`。只有控制者能释放控制权。输入支持控制字符，每帧最多 16,384 个字符；背压失败前可能已经发送了一部分。不要自动重试按键。尺寸范围为 1–1,000。

断开连接移除 participant，不关闭终端。已退出终端仍可查看，删除前仍计入 32 个会话的上限。DELETE 终止原生会话的作业；刻意 daemonize 的独立 OS 会话有自己的生命周期。App 关闭时关闭所有终端，PTY/输出不跨重启保留。OpenAPI 的 `x-interactive` 描述传输 schema。

## 页面在线状态

连接 `/api/presence/connect`，使用与终端相同的首帧认证。`features.page_presence` 表示支持；GET `/api/presence` 可只读当前目录而不加入。每个标签页都有自己的 participant ID，即使显示名称相同。

报告当前聚焦的面板：

```json
{
  "kind": "presence",
  "display_name": "Alice",
  "color": "#112233",
  "foreground": true,
  "focus": {
    "target": {"kind": "conversation", "thread_id": "thread-example"},
    "root_thread_id": "thread-example"
  }
}
```

目标可以标识工作台页面、对话、Project、资源、原生文件、Git 比较或终端。路径使用 Files/Git 返回的规范路径。所属根与聚焦目标分开。不可用目标保留标识和原因；presence 不打开替代目标，不读取文件内容，也不启动终端。

变化时立即报告，隐藏/失焦时设置 `foreground:false`，并至少每 20 秒重发一次。60 秒没有报告会以 4408 关闭。最多 32 个 participant，每份报告最多 16 KiB。无效输入返回 `presence_invalid`，不替换已接受状态。

`PresenceFrame` 包含目录和 `same_page_participant_ids`；按目标匹配，不按滚动位置或布局匹配。HTTP 可接受 participant ID 来获取相同分组。资源可用性每 15 秒及成员变化时刷新。断开连接/重启清除成员。Presence 表示连接活动，不是已验证身份或同步导航，并独立于草稿和执行。

## 共享输入框

只有客户端需要共同编辑未发送输入时，才实现此协议。普通 HTTP 脚本直接提交 `parts` 即可。

GET `/api/drafts` 以 `{thread_id,draft_id,unsent_since}` 列出非空草稿，不返回文本或编辑者成员。选中的 pending/failed 附件算作非空，空白文本和休眠条目不算。收到 `draft` 摘要提示或 reset 后重新获取，再另行查找 Thread 元数据。索引和文档都在内存中，App 重启后消失。

使用首帧认证连接 `/api/threads/{thread_id}/draft/connect`。每个根 Thread 在一个 App 中支持一份共享文档，与 computer sharing 独立。`DraftFrame` 包含 `draft_id`、participant ID、base64 Yjs v1 完整状态更新和 presence。使用以下根类型：

| 根                               | 含义                                                               |
| -------------------------------- | ------------------------------------------------------------------ |
| `text`（`Y.Text`）               | 包含内联附件 token 的纯文本                                        |
| `attachments`（`Y.Map<string>`） | `inline-<UUID>` 键映射到同 Thread 的附件 ID，或 `pending`/`failed` |

内联文本 token 为 `U+FFFC + key + U+FFFC`。只有实际出现的 token 选择输入；休眠 map 条目用于撤销。缺失或未完成条目阻止提交。发送前按文本顺序展开出现项，避免编辑器 token 进入执行。可用附件元数据端点查看附件，无需下载字节。

将服务器更新应用到兼容 Yjs 的副本。发送 `{"kind":"sync","draft_id":"...","update_base64":"..."}`，其中必须是副本的**完整**更新，包含依赖和删除。服务器原子校验合并后的文档。`draft_invalid` 保留服务器状态；保留被拒绝的本地内容以便修正。上限为 512 KiB 编码 CRDT 状态、256 Ki 文本字符、八个已选附件及 20 MiB 附件字节。CRDT 历史计入状态上限。

编辑者 presence 使用 `{"kind":"presence","draft_id":"...","presence":{"name":"Alice","color":"#112233"}}`；可选 `anchor`/`head` 为 base64 Y relative positions。名称和颜色未经身份验证。重连时只有 `draft_id` 相同才能合并离线编辑；身份丢失后应显式提供本地恢复选项。

通过普通 `/submit` 发送，不使用独立草稿端点：

1. 同步编辑，并观察其已接受 CRDT 状态。
2. 克隆副本，将文本/附件选择展开为有序 `parts`。
3. 明确收到提交成功确认后，只删除该克隆中可见的文本，再将完整删除更新合并到实时副本。保留内联注册项，以支持并发出现的 token。
4. 请求被拒绝或传输结果不确定时保留草稿。编辑状态重放可以核对合并；重新提交执行是另一个操作。

断开连接移除编辑者 presence，保留内存文档和活跃 Run。关闭 App 会丢弃草稿；保存的对话历史有独立生命周期。

### 有序输入体

根 submit 和根 steer 按编写顺序接受字符串和附件引用：

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

先暂存字节。引用是 Thread 附件 ID，不是 Host 路径或编辑器 token。根输入接受 `parts`，不接受 `prompt`/`attachment_ids`。最多 1,024 个 part、256 Ki 编写文本字符，并受附件上限约束。空输入被拒绝；纯附件输入有效。

图片转为原生图片输入，普通文件保留为可读引用。符合条件的文件/diff 捕获会加入带来源的内联文本。展示元数据包含 `source_id` 和 `harness_ui.composer.index`；一个附件可展开为多个片段，共享其编写索引。按 source/index 分组，不要只按附件 ID 分组。

## 观察实时通道与恢复缺口

通过 WebSocket 连接 `/api/realtime/connect`，然后发送 `{"api_key":"<instance key>"}`。使用唯一 channel ID 订阅：

```json
{"version":1,"kind":"subscribe","channel":"focused-root","stream":"focus","root_thread_id":"<thread ID>","after":null}
```

使用 `stream:"summary"` 且不提供根 ID，可接收失效提示。每个根和 summary 各可有一个通道。活跃根有预留容量，其他通道共用十二个空闲槽位。Envelope 包含 `version:1`、channel 和 frame。通过 `{"version":1,"kind":"unsubscribe","channel":"focused-root"}` 退订，以 `{"version":1,"kind":"pong"}` 回答 ping。

按顺序实现 focus 流启动：

1. 应用 `snapshot`。如果存在 `snapshot.root_stream`，其游标为 null，随后会收到带索引的 `root_stream` 批次（每批最多 16 个事件）。
2. 将这些事件恰好一次应用到精确 Run 的临时展示。收到 `ready` 后保留 `resume_cursor`。若在 ready 前断开，丢弃未完成的启动状态并重新开始。
3. 先应用后续 `event` 帧，再保存其游标。没有根重放时，初始 snapshot 已提供游标。
4. 另行获取已保存 transcript；选中 continuation 前进后，用它替换临时输出。不要再次追加诊断用的 `recent_events`。

只有客户端保留了展示状态，才通过 `after` 携带不透明游标重连。新页面需要重新初始化。Observer 索引和 hub 序号不同，这也不是 Service 的 `Last-Event-ID` 协议。子级重连视图显示已关闭活动，不显示未完成的子级输出。

Summary 先发送 `open`，再发送 `invalidation`。`resumed:true` 会重放漏掉的提示，false 则要求初始核对。刷新受影响资源，可通过 POST `/api/threads/activity/lookup` 每批查询最多 100 个 ID。只有活动列表的第一页包含活跃工作。根操作结束通知可能含 receipt/status 和 320 字符简报；按 epoch/receipt 去重，并避免在 reset 后通知历史完成事项。

收到 `reset` 后重新获取状态并发起新订阅。Focus 和 summary 游标分别绑定各自作用域/epoch；稀疏序号有效。缓冲有界且仅存在于当前进程，没有持久重放或 SSE 回退。断开连接只停止观察，不停止执行。各通道独立恢复。

## 当前路由索引

完整端点清单（包括 Browser、Live 和 MCP Apps）以运行实例 OpenAPI 为准。以下入口也常用于集成：

| 请求                                            | 用途                                        |
| ----------------------------------------------- | ------------------------------------------- |
| `GET /api/setup`                                | 查看初始化就绪状态                          |
| `POST /api/setup/preview` 和 `/api/setup/apply` | 预览并应用初始化选择                        |
| `POST /api/environments/preflight`              | 对 Project 路径测试 Full Control 或 Sandbox |
| `GET /api/projects`                             | 读取可用 Project 和创建默认值               |
| `GET /api/threads`                              | 查询并分页读取根                            |
| `GET /api/host/terminals` 和 `/{terminal_id}`   | 列出终端或查看一个终端                      |
| `GET /healthz` 和 `/readyz`                     | 检查监听器存活与 App 就绪                   |

功能标志描述可用性：`host_files` 要求共享，`host_git` 还要求 Git，`host_terminal` 要求支持的原生终端。`shared_drafts` 和 `page_presence` 描述独立于共享的内存协作功能。Health、readiness、静态资源和 OpenAPI 下载各有自己的边界，不列入普通 schema 路由。

## 错误与版本

读取 `{"error":{"code":"...","message":"..."}}` 中的 code，不解析消息文本：

| 状态                  | 通常应做什么                                    |
| --------------------- | ----------------------------------------------- |
| 400                   | 修正无效输入或被拒绝的 Host 地址                |
| 401                   | 重新认证                                        |
| 403                   | 检查 Origin 或操作的访问/功能边界               |
| 404                   | 刷新缺失资源或进程内回执                        |
| 409                   | 重新获取版本、continuation、revision 或就绪状态 |
| 413                   | 减小请求或操作规模                              |
| 422 `request_invalid` | 修正查询参数                                    |
| 503                   | 检查 App/runtime 就绪或关闭状态                 |

超时或响应断开会使修改结果不确定。重复操作前先检查当前状态。支持多个 Harness UI 版本时，检查 `/api/status` 和运行实例的 schema。
