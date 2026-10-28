---
title: 通过 MCP 管理 Service
description: 连接外部客户端，通过 MCP 更新工作空间资源并排查运行问题。
---

将支持 MCP 的客户端连接到 Service，即可管理工作空间资源并查看执行情况。先列出 agent 或搜索已安装版本的文档，再用同一连接读取资源，携带返回的 ETag 完成更新。

## 使用工作区密钥连接

准备提供 API 的 Service 进程（`control` 或 `all`）、可访问的地址，以及有权执行目标操作的[工作空间 API key](identity.md#api-keys)。仅运行 worker 的进程不提供 MCP。

1. 在客户端添加 **Streamable HTTP** MCP 连接。
2. 将 URL 设为 Service 地址加 `/api/v1/mcp/`。
3. 把工作空间 key 存入客户端的秘密存储，并通过 Bearer 请求头发送。
4. 连接并发现工具。调用 `search_documents` 或列出 agent 的工具，确认连接可用。

连接信息如下；请转换为客户端要求的配置格式，并替换 key 占位符：

```json
{
  "url": "https://service.example.com/api/v1/mcp/",
  "headers": {"Authorization": "Bearer REPLACE_WITH_WORKSPACE_KEY"}
}
```

工具发现和调用都需要 key，不接受浏览器 Cookie。key 保留原有的工作空间范围和权限；可选的 `X-Workspace-ID` 必须指向同一工作空间。凭证放在连接配置中，不作为工具参数。

通过代理部署时，将 `server.public_url` 设为外部可访问的 Service URL，并保留公开 Host。MCP 按入口配置检查 Host 和 Origin。请求无状态，因此副本间无需 MCP 粘性会话，也无需第二个 Service runtime。

## MCP 提供哪些能力

根据任务选择传输方式：

| 任务                                                                               | 传输方式                                                                  |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 管理 agent、修订、模型、供应商、skill、连接、环境、记忆、资产元数据和 webhook 订阅 | MCP                                                                       |
| 配置计划、预览时间、启用/暂停/归档、手动触发和读取触发历史                         | MCP；参阅[计划](schedules.md)                                             |
| 读取会话、线程、输入队列条目、运行、执行尝试、展示内容、用量、trace 和 span        | MCP                                                                       |
| 提交发现项，读取发现项或分析历史                                                   | MCP；参阅[执行问题](agents-and-runs.md#find-and-improve-execution-issues) |
| 搜索随包文档                                                                       | MCP `search_documents`                                                    |
| 提交 agent 输入、恢复/取消/等待执行，或跟踪实时输出/SSE                            | 直接 HTTP                                                                 |
| 上传或下载文件及原始二进制内容                                                     | 直接 HTTP                                                                 |
| 管理组织、成员或权限；浏览器登录和 OAuth 流程                                      | 直接 HTTP                                                                 |
| 审阅发现项、准备 Finding Agent 或启动分析                                          | 直接 HTTP                                                                 |

从实际部署发现准确的工具名和 schema。工具来自组装后的 OpenAPI 中显式允许的操作，包括已允许的 Distribution 扩展。名称由 operation ID 派生；新增 HTTP 路由只有显式允许后才会出现。MCP 不增加权限或审批权，例如订阅管理仍需要工作空间 `admin`。

## 读取和更新资源

用一份你有权编辑的独立记忆练习：

1. 发现读取和更新记忆的工具。
2. 按 ID 读取记忆。保存 `body.id` 和 `headers.etag` 的完整值，包括引号。
3. 调用更新工具，传入 ID、以 `expected_version` 携带该 ETag，并把修改放在 `request_body` 下。
4. 再读一次记忆，确认指南已保存。

例如，以下更新参数会恢复继承部署的默认指南。请将两个占位值替换为读取响应中的实际值：

```json
{
  "memory_id": "mem_REPLACE_WITH_RETURNED_ID",
  "expected_version": "mem_REPLACE_WITH_RETURNED_ID:1",
  "request_body": {"guide": null}
}
```

路径和查询参数保留 HTTP 名称。请求头参数使用小写 snake_case，例如 `Idempotency-Key` 对应 `idempotency_key`。`If-Match` 前置条件使用 `expected_version`，接受带或不带引号的 `{id}:{version}`（模型为 `{key}:{version}`）。旧的 HTTP 请求头拼写不再作为工具参数接受；未知参数会报错并列出可用名称。

JSON 请求体嵌套在 `request_body` 下；省略字段、空对象和显式 `null` 各有含义。仅接受空对象的请求体（例如计划的 `enable` 命令）可以省略，此时发送 `{}`。

API 工具结果包含 `status`、`headers` 和未经改写的 JSON `body`。原响应有对应请求头时，返回小写的 `etag`、`x-request-id` 和 `retry-after`；空 `204` 响应的 `body` 为 `null`。分页信息保留在 `body` 中。HTTP 失败会转为工具错误，保留相同的 Service 错误封装。

缺少 `expected_version` 返回 `428`，过期值返回 `412`。重新读取资源，协调修改后再尝试。MCP 不自动重试写入，也不生成幂等键。响应丢失后，先检查资源，确定写入是否已生效。

## 检查失败的运行

1. 发现执行查询工具，按 ID 读取运行。需要先定位时，使用会话或线程查询。
2. 读取执行尝试和展示内容，找到执行停止的位置。同时检查运行状态：失败的执行尝试可能属于已经恢复的运行。
3. 查询运行的 trace 和 span，取得诊断证据。向运维人员求助时，保留运行 ID 和相关错误。

这些查询保留 HTTP 的工作空间权限、租户过滤和脱敏规则。恢复或取消使用直接 HTTP，实时输出使用 SSE。[监控指南](monitoring.md#troubleshoot-a-request-or-run)解释诊断字段。

## 搜索已安装版本的文档

调用 `search_documents`，提供关键词、1 到 10 的 `limit`，以及 `language`（默认 `en`，也可选 `zh-CN`）：

```json
{"query": "上传 skill", "limit": 3, "language": "zh-CN"}
```

查看结果中的已安装版本、源文档、章节路径、源行号和文本片段。`truncated` 表示省略了匹配项或片段内容；空 `results` 数组表示没有匹配。结果太宽泛时，细化关键词。

搜索离线读取已安装 Service 版本随包提供的标题、章节标题和 Markdown，无需模型或网络搜索。指向 `docs/a13n-service/` 外的链接可能属于其他版本。包内包含 API 参考入口页，不含网站生成的操作页面。完整 HTTP schema 请读取本部署的 `/api/v1/openapi.json`。

## 通过 HTTP 上传和下载

导入 skill 时，先用 HTTP 传 ZIP 字节，再用 MCP 执行 JSON 资源操作。客户端需要自己的 HTTP 或 shell 能力，以及具备相应权限的 key。

将 `A13N_URL` 设为 Service 地址，`A13N_API_KEY` 设为工作空间 key，`UPLOAD_KEY` 设为本次上传的新键，并在重试时保留。准备有效的本地 `release-notes.zip` [skill 包](skills.md#add-a-skill)，执行：

```sh
curl --fail-with-body "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: $UPLOAD_KEY" -F file=@release-notes.zip
```

从响应取得 `upload_id`，传给发现的创建 skill 的 MCP 工具：

```json
{"request_body": {"source": {"kind": "upload", "upload_id": "upl_REPLACE_WITH_UPLOAD_ID"}}}
```

下载修订时，将 `SKILL_ID` 和 `REVISION_ID` 设为返回的对应标识符。输出文件使用不同名称，避免覆盖源 ZIP：

```sh
curl --fail-with-body \
  "$A13N_URL/api/v1/skills/$SKILL_ID/revisions/$REVISION_ID/content" \
  -H "Authorization: Bearer $A13N_API_KEY" -o downloaded-release-notes.zip
```

上传限制和消息附件参阅[上传与资产](files-and-webhooks.md#uploads)。

## 通过 HTTP 执行 Agent 并观察结果

用 MCP 选择 agent，用 HTTP 提交输入。将 `AGENT_ID` 设为该 agent 的 ID，`MESSAGE_KEY` 设为这条消息的新键。工作空间 key 必须有执行权限。只在重试同一次提交时复用 `MESSAGE_KEY`：

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Summarize these notes.\"}]}}"
```

将 `thread.id` 和 `entry.id` 保存为 `THREAD_ID` 和 `ENTRY_ID`。输入排队时，响应中的 `run` 可能为 null。读取条目，直到 `assigned_run_id` 指出对应运行，再设置 `RUN_ID` 并读取运行：

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
curl --fail-with-body "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

分配结果标明处理输入的运行，不代表执行完成。`completed` 时读取 `output`；`waiting` 时收集所需回答、审批或工具结果，再显式恢复。分别处理 `failed` 和 `cancelled`。后续输入和恢复可能产生后继运行，因此应跟踪输入及其运行，而不是只看对话的第一次运行。客户端断开连接后，执行仍会继续。

实时输出可通过[会话事件流](agents-and-runs.md#follow-a-session-stream)跟踪。完整 HTTP 流程及 SDK 选择参阅[应用集成](connect-application.md)。
