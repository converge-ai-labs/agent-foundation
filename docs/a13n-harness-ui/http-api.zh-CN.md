---
title: Harness UI HTTP API
sidebarTitle: HTTP API
description: WebUI 服务器的 HTTP API，用于初始化、对话、实时输出和 Host 面板。
---

WebUI API 使用与 TUI 相同的 App。调用者共享实例密钥，回执属于当前进程，实时事件尽力投递。它不是 Service 的托管 `/api/v1` API。从 [WebUI](webui.md) 开始，不需要监听器时用 [Python 嵌入](embedding.md)。

## 认证与查看协议约定

将 `HUI_URL` 设为监听器的源，将 `HUI_API_KEY` 设为配置的访问密钥：

```bash
curl --fail-with-body "$HUI_URL/api/status" \
  -H "Authorization: Bearer $HUI_API_KEY"

curl --fail-with-body "$HUI_URL/api/openapi.json" \
  -H "Authorization: Bearer $HUI_API_KEY"
```

状态响应包含 `api_version: "1"`、软件包与构建信息、App 状态、访问模式和功能标志。运行中的 OpenAPI JSON 描述精确的请求、响应模型和约束。Swagger 和 ReDoc 页面已禁用。[已检查的 schema](/reference/harness-ui-openapi.json)从源码版本生成，不能证明其他运行版本采用相同约定。

认证及 HTTP `Host`/`Origin` 请求头校验在监听器边界执行。请使用支持请求头的 HTTP/fetch 客户端。访问密钥不能放在 API 查询字符串或日志中，也不要将 `/api/auth/*` 管理的模型 provider 凭据与监听器密钥混淆。`webui --dangerous-skip-permissions` 选项会关闭监听器认证；它不能作为生产认证机制。

## 创建 Thread 并提交输入

先查看 `/api/selectors` 和 `/api/setup`，确认已接受配置可用。运行前需配置 Agent、Model 凭据和 Environment profile；以下示例使用默认选择，可能触发模型和工具的副作用。

```bash
curl --fail-with-body "$HUI_URL/api/threads" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}'
```

将返回的 `thread_id` 保存为 `THREAD_ID`：

```bash
curl --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/submit" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"parts":["Explain this project without changing files."]}'
```

`/submit` 接受仅用于本次 Run 的 `model_id`、`thinking`、`fast` 和 `reasoning_mode`。省略/null 控件继承 Model 设置，`thinking: false` 请求 Off。从 `/api/selectors` 读取支持值和禁用原因，无效选择拒绝而不回退。这些覆盖不修改 Thread 保存的默认值，引导消息也不接受这些覆盖。配置检查分别显示 Run 实际使用的参数和当前默认值。

返回的 `RootRunReceipt` 包含 `receipt_id`、`thread_id` 和 `submitted_at`。读取 `/api/operations/{receipt_id}`，直到操作达到终态；根操作没有 HTTP `wait` 端点。准备中或运行中不代表已完成。Completed、suspended、failed、cancelled 描述操作状态；还需分别查看 `outcome.execution`、`outcome.continuation` 和 `outcome.environment`。

每个 Thread 只允许一个活跃根操作，第二次提交会被拒绝，不会排队。这里没有 Service 式持久接收或幂等约定。确认响应丢失后，先读取当前 Thread 和根操作活动，再决定下一步，不要盲目重复提交。进程重启后，旧回执可能不可用，但保存的 continuation 仍可读取。

## MCP 输入与集成控制

这些需要认证的路由独立于延迟决策和根 Run 受理：

| 路由                                                               | 行为                                 |
| ------------------------------------------------------------------ | ------------------------------------ |
| `GET /api/threads/{thread_id}/mcp/integrations`                    | 当前保留的连接代，不尝试建立连接     |
| `POST /api/threads/{thread_id}/mcp/integrations/{server_id}/close` | 显式关闭所属 Thread 的代，返回 204   |
| `GET /api/threads/{thread_id}/mcp/inputs`                          | 该 Thread 及后代子 Thread 的输入状态 |
| `POST /api/threads/{thread_id}/mcp/inputs/{request_id}/response`   | 回答确切实时请求，不启动另一次 Run   |

表单响应为 `{"action":"accept","content":{"name":"Ada"}}`，URL 确认为 `{"action":"accept"}`，也可使用 `{"action":"decline"}` / `{"action":"cancel"}`。检查请求模式和原 schema。响应视图只确认输入已接受，不代表 MCP 工具调用已完成；继续观测原操作。完全相同的重复答案核实结果，冲突答案失败。过期、已退役或不可用请求不能授权后续发送。交付不确定时，查询并仅重试确切答案，不要重新提交业务操作。MCP 请求状态属于进程内状态，重启后消失。见[人工输入](mcp.md#human-input-from-mcp-servers)和 [Python 适配器](embedding.md#mcp-input-and-integration-control)。

## 记忆观测

`GET /api/threads?memory=true` 只选择 Memory Thread，默认列表不包含它们。Project 筛选和分页保持常规语义；`projectless=true` 选择 Global 记忆。`POST /api/threads/lookup` 接受相同的 `memory` 区分字段。Memory Thread 暴露 `memory_scope`，支持现有的对话记录、实时、配置检查和用量读取；修改或执行控制返回 `memory_thread_read_only`。

`GET /api/memory/files` 列出当前 Global 记忆文件。添加 `project_id` 可选择已配置 Project 的范围。`GET /api/memory/file?path=MEMORY.md` 读取范围内相对路径文件，也接受同一可选 Project 选择字段。这些路由需要认证、只读、独立于原生计算机共享；Memory 禁用时不可用。它们不暴露任意 Host 路径或内部管理信息，也不会触发自动记忆整理。

## Coordinator

`ThreadSummary.role` 为 `ordinary`、`coordinator` 或 `worker`。`coordinator_thread_id` 标识 worker 的所属 Coordinator，其他情况为 null；`auto_followup` 是 Coordinator 通知设置，其他情况为 null。Project 可包含多个 Coordinator，不暴露单例 lead 字段。

用 `POST /api/threads` 创建 Coordinator，例如 `{"defaults":{"project_id":"project-main"},"coordinator":true}`。需要可用 Project，默认开启自动跟进。创建保存角色，不启动 Run；另行提交输入。提交失败保留 Coordinator。创建不确定时按保留的 `thread_id` 核对，不创建新身份。

`POST /api/threads/{thread_id}/coordinator` 将已有、关联 Project 的普通根线程提升为 Coordinator。需要已接受的 Project、未归档且不活跃的 Thread，并且没有待处理决策。对于已符合条件的 Coordinator，此操作幂等，返回 `ThreadSummary`，不启动 Run 或模型调用。提升保留身份、历史、标题和设置。Worker 和子线程不能提升；没有降级或接管 worker 的 API。

`PATCH /api/threads/{thread_id}/coordinator` 传入 `{"auto_followup": false}` 暂停该 Coordinator 的自动生命周期通知，true 启用。更新采用最后写入生效，并发布 Thread 失效通知。[Sidekick 配置](configuration.md#webui-sidekick)不限制提升或跟进。归档 Coordinator 保留角色，恢复使用普通的预期版本元数据 API。Coordinator 和 worker 不能修改或清除所属 Project。

活动筛选用 `coordinator_thread_id` 选择某个 owner 的 worker，或用 `independent_only=true` 排除 worker。筛选绑定游标，未筛选的人工搜索包含全部根。创建 worker 时，向 `POST /api/threads` 提交 `coordinator_thread_id`，在 `defaults` 选择 owner 的同一可用 Project。owner 必须未归档；归属永久保存，与 `coordinator: true` 互斥。人工创建使用提交默认值，不用 Sidekick。创建不启动执行；首次人工提交在启用跟进时安排尽力投递的 owner 通知。保留客户端 `thread_id` 以核对不确定创建，提交失败仍保留归属。人工查看和执行使用普通根 API。

旧 Project `/lead` 路由和字段已移除。启动迁移一次性转换已有 lead 绑定和所属关系，保留 ID 与历史；旧的禁用绑定转换为关闭自动跟进的 Coordinator。其他保存的对话仍为普通根线程。

## 单次运行执行环境

`POST /api/threads/{thread_id}/submit` 在有序输入部分之外，还接受可选 `environment` patch：

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

Full Control 使用 `environment-native`，Sandbox 使用 `environment-sandbox`，也可使用 `GET /api/selectors` 返回的 profile ID。省略的配置维度沿用 Thread 保存的选择；空集合清除选择，null 只清除 `default_environment`。显式选择只影响本次 Run，不更新 Thread 配置或版本。返回回执代表接收成功，不代表 Environment 准备成功：需查看操作中的 profile 不可用或运行时失败。两种情况都不会悄悄回退到 Full Control。引导请求拒绝此字段。延迟回复保留暂停 continuation 捕获的 Environment 选择；之后不带覆盖的普通提交再次使用已保存默认值。

## 设备连接与工作环境

Device 资源描述可复用连接，不是 Run 或文件系统根目录。通过 `PUT /api/configuration/sources/devices/build.yaml` 创建，参阅 [Device YAML](environments-and-projects.md#add-device-bindings)。Device 凭据与 WebUI 监听器密钥分开。

| 路由                                       | 行为                                                     |
| ------------------------------------------ | -------------------------------------------------------- |
| `GET /api/devices`                         | 返回已配置 ID、名称和载体类型，不尝试连接                |
| `GET /api/devices/{device_id}`             | 检查可用性，读取路径格式、默认工作目录和目录发现支持情况 |
| `GET /api/devices/{device_id}/directories` | 浏览一层目录，不打开 Envd Session                        |
| `WS /api/devices/{device_id}/connect`      | 原生守护进程反向挂接，不是 WebUI 交互通道                |

目录查询接受 `path`、`offset`（0–1,000,000）和 `limit`（1–200，默认 100）。省略 `path` 使用 Device 默认工作目录；下一页使用返回的 `next_offset`。路径是 Device 上的绝对 EIP 路径，不是浏览器或监听器本机路径。可用性和目录发现独立：在线 Device 可以禁用发现，目录不可读不代表设备离线。元数据和浏览不会创建 Thread 或 Envd Session。这些路由不依赖原生计算机共享。

守护进程的反向连接协商 `eip.v1`，并在 HTTP upgrade 请求中发送 Device bearer 凭据。它不使用 WebUI 首帧认证。不要通过查询参数发送凭据。

Thread 创建默认值和配置 patch 接受 `environment_bindings`、`default_environment`，它们独立于 `environment_profile_id`：

```json
{
  "project_id": null,
  "environment_bindings": [
    {"device_id": "device-build", "alias": "build", "working_directory": "/work/repository"}
  ],
  "default_environment": "build"
}
```

配置预览直接使用此请求体；创建 Thread 时放在 `defaults` 下。Device 离线时仍可保存已知绝对目录。每次 Run 捕获选择，并准备新的执行专属 Envd Session；连接已配置不保证执行成功。`default_environment` 决定相对路径和省略 cwd 时的路由，不构成访问限制边界。原生 Files、Changes 和 Terminal 路由仍操作监听器 Host，不操作所选 Device。

### 移除离线配置并修复选择

`DELETE /api/configuration/sources/devices/build.yaml` 移除本地连接资源，不联系 Device，也不删除远程文件。同一配置源删除 API 可移除自定义 Environment profile。内置 profile 不是可删除源文件。请先通过普通配置源更新移除当前 Project 和全局默认值的引用，否则候选配置校验会拒绝删除。

已有 Thread 选择和历史捕获不会被级联修改。查看 `/api/threads/{thread_id}/configuration`，再携当前 `expected_version` 显式 PATCH 后续选择。例如，同时移除缺失 Device 并替换默认环境：

```json
{
  "expected_version": 1,
  "patch": {"environment_bindings": [], "default_environment": "thread-files"}
}
```

选择了本地根目录的 Thread 也可用 `workspace` 作为替换。移除所选默认环境却不提供替换会失败，不会悄悄将后续工具转向其他环境。移除配置或修复后续选择，不会重写活跃捕获、已保存 Run、对话记录或 continuation。

## 对话输入导航

`GET /api/threads/{thread_id}/inputs` 返回绑定 continuation 的分页普通输入轮次目录，不包含引导和隐藏系统输入。每轮包含稳定输入身份、有界预览、输入位置、不包含边界的结束位置，以及可选记录的最终回复位置。`limit` 为 1–100；跟随 `next_cursor` 读取目录，无需传输工具输出。

`GET /api/threads/{thread_id}/transcript` 的首个页面接受可选 `turn_id`。`next_cursor` 读取更早消息，`newer_cursor` 读取更晚消息。两个游标和 `expected_continuation_id` 都绑定所观测历史。Continuation 不匹配后应刷新，不可混合不同历史头的位置。页面还包含相交的 `turns`，以及为显示原始输入和最终回答而需要的普通页面范围外 `boundary_entries`。按位置去重条目。中间条目缺失只表示尚需分页读取，并不证明该轮没有过程输出。最终位置来自成功保存的执行，不来自最后一段 assistant 文本或实时 text-end 事件。

## 原生终端

Linux/macOS 的原生计算机共享包含真正的交互式终端。请检查 `features.host_terminal`；Windows 返回 false 和 `host_terminal_unavailable`，不会替换成非交互终端。Shell 缺失时也不可用。禁用共享返回 `host_terminal_disabled`。通过 `POST /api/host/terminals` 和 `{"cwd":"/work","rows":24,"columns":80}` 等 JSON 创建。可选 `project_id` 必须指定已接受的 Project。返回的 `terminal_id` 属于此次 App 生命周期，不是 Thread 或 Run ID。`cwd` 记录初始本机目录，不跟随后续 WebUI 导航变化。

使用同一监听器源连接 `ws(s)://<listener>/api/host/terminals/{terminal_id}/connect?cursor=<last-end>`。游标可选，不含凭据。十秒内发送首个文本帧 `{"api_key":"<instance key>"}`。接受连接前检查 HTTP `Host` 请求头和精确的 `Origin` 请求头，认证在资源查找前完成。密钥错误或缺失以 4401 关闭；`Host` 或 `Origin` 请求头被拒绝时握手失败。使用 `--dangerous-skip-permissions` 时发送 `{}`。不要使用 URL 密钥、cookie 或模型 provider 凭据。

生成的 OpenAPI 文档中，`x-interactive` 节引用认证、命令、输出和错误 schema。认证后，服务器发送 `TerminalFrame`，包含连接内 `participant_id`、当前 `terminal` 视图和原始 base64 输出。相邻帧之间保留流式 UTF-8 解码器；字节位置不是字符偏移量。保存 `end` 作为下次重连游标。最多保留 1 MiB；`gap: true` 表示字节缺失或所提供游标超前，不代表已恢复完整屏幕。

每个连接初始均为查看者。使用最近观测的 `control_epoch` 发送命令：

```json
{"kind":"control","control_epoch":0}
```

领取或接管成功会增加 epoch，并广播新控制者。获得 epoch 1 后，输入和调整尺寸如下：

```json
{"kind":"input","control_epoch":1,"text":"pwd\n"}
{"kind":"resize","control_epoch":1,"rows":40,"columns":120}
```

输入支持控制字符，例如 Ctrl+C 的 `\u0003`。使用 `{"kind":"control","control_epoch":1,"release":true}` 释放控制。只有当前控制者可以释放；其他查看者可使用当前 epoch 显式接管。过期命令返回 `host_terminal_control_conflict`。每帧输入最多 16,384 个字符，行列数范围为 1–1,000。背压可能在部分投递后返回 `host_terminal_input_failed`。不得自动重试按键，包括连接或确认响应丢失后。

断开只移除该参与者并释放其控制。重新加入获得新参与者 ID 和保留输出。进程退出后仍可检查 `exited`；显式 HTTP DELETE 关闭 PTY、终止原生会话任务，并移除身份。刻意守护进程化的独立操作系统会话不属于此生命周期。关闭前最多可存在 32 个终端会话，包括已退出的终端会话。App 关闭会关闭全部终端会话；重启不保留 PTY 或输出。保存的对话历史不受影响。

## 页面在线状态

`features.page_presence` 表示支持 App 全局的临时参与者目录，即使没有打开 Thread。`GET /api/presence` 返回当前目录快照。连接 `/api/presence/connect`，使用与原生终端连接相同的首帧认证。每个连接获得新的 `participant_id`；两个标签页使用相同名称，仍是不同参与者。名称和颜色不能验证作者身份，也不授予权限。

认证后发送 `PresenceReport`，描述标签页当前聚焦面板：

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

目标使用已有 App 视图的语义身份：工作台 `home`/`settings`/`catalog`、对话、Project、已配置资源、本机文件、Git 比较或终端。本机路径属于服务器；请使用 Files/Git 返回的规范路径。可选的容器 `root_thread_id` 与聚焦目标是不同字段。Changes 目标使用精确仓库根目录、可选仓库相对路径和比较类型。缺失或禁用目标仍保留在报告中，标为 `availability: unavailable` 并给出原因；服务器不会导航到替代目标。在线状态只检查可用性，不读取文件内容，也不创建终端或修改 Agent Environment。

标签页隐藏或失焦时设置 `foreground: false`。变化时立即报告，并至少每 20 秒重发当前报告，即使没有变化。60 秒没有报告的连接以 4408 关闭，并失去成员资格。这衡量传输参与情况，不代表人或 Agent 是否活跃。最多 32 个在线参与者，报告最多 16 KiB。无效报告返回 `presence_invalid`，不替换已接受状态。

`PresenceFrame` 携带目录，以及相对于当前连接的 `same_page_participant_ids`。匹配依据聚焦目标，不依据滚动位置、布局或所属 Thread。HTTP 可提供 `participant_id` 获取相同分组，不改变成员资格。成员变化时及每 15 秒刷新快照，重新检查资源可用性。断开只移除在线状态；重连报告新的当前位置，不报告导航历史。App 重启清空目录。客户端忘记访问密钥时，会关闭交互连接。

页面在线状态独立于草稿编辑器光标、已保存评论和执行观测。聚焦 Files 不会清除或移动 Thread 草稿。打开协作者的位置是显式的个人导航操作；没有跟随模式或强制滚动。

## 已保存输出的评论

`features.output_comments` 提供可编辑的人类评论，**仅针对已保存、可见的 assistant 文本**。WebUI 当前不显示评论控件；只有 API 客户端使用这些路由。它不会将实时或未保存输出持久化。根对话记录的 assistant 部分包含可为 null 的 `comment_target` 和显式 `text_truncated` 标志；截断的显示片段不能用作精确选区来源，应获取原始文本窗口。User、tool、thinking 部分，以及初始或未保存历史没有这些字段。已保存子运行检查使用 `GET /api/threads/{parent_thread_id}/children/{execution_id}/saved-output`，每页最多 20 个块，`next_cursor` 绑定来源。请原样使用这些目标快照，不要猜测索引，也不要将实时事件转成对象引用。

通过 `POST /api/threads/{root_thread_id}/comments` 发布：

```json
{
  "comment_id": "comment-0123456789abcdef0123456789abcdef",
  "target": {
    "producing_thread_id": "thread-example",
    "source_id": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "location": {"kind": "root_text", "message": 1, "part": 0}
  },
  "author": {"display_name": "Alice"},
  "body": "Please explain this conclusion."
}
```

每条拟发布评论只分配一次新的客户端身份：`comment-` 后接 16–64 个字母、数字、下划线或连字符（可用 UUID 十六进制值）。正文和所选引用各最多 16,384 个字符，显示名称最多 80 个。可选作者 `participant_id` 只是未经验证的关联信息，不要求保持连接。省略 `selection` 表示评论整个块；否则提供 `{"start":0,"end":5,"quote":"exact"}`，必须匹配 Markdown 渲染前原始来源的 Unicode 码点范围。JavaScript UTF-16 或 DOM 偏移量必须转换；选区不明确时应使用整块评论。超长文本会被拒绝，不会截断成另一个锚点。

SQLite 提交后才返回确认。相同身份和规范化发布内容重复请求会返回原始记录与创建时间，重连或重启后也相同。同一身份使用不同内容返回 `409 comment_identity_conflict`。响应丢失后通过 GET 或重复使用同一身份核对，不能自动分配新身份。首次发布在提交时重新检查来源选择，已变化时返回 `409 comment_target_stale`。后续 Run 执行后，已有评论的块仍是有效的保留目标。发布评论不接收 Run，也不修改 Thread 元数据或配置版本、continuation、决策或模型消息。

`GET /api/threads/{root_thread_id}/comments` 列出全部评论，包括旧来源评论，接受 `limit`（1–100，默认 20）和不透明游标。默认按创建时间、评论身份升序排列；`newest_first=true` 反转两者，显示最新讨论在前。游标绑定排序方向。可选 `target` 是精确目标的 JSON 编码；游标绑定 Thread 和筛选条件。GET `.../comments/{comment_id}` 读取一次发布。没有历史时返回空集合。实时摘要通道在提交后尽力发送 `kind: comment` 失效通知；新订阅或 reset 后应重新核对，不要将其游标当作持久评论游标。

向 `/api/threads/{root_thread_id}/saved-output` POST 目标，获取原始文本。`offset` 和 `limit` 最多选择 65,536 个 Unicode 码点，`total_characters` 和 `next_offset` 表明截取情况。只能读取该 Thread 家族中当前选定或因评论保留的目标，不能读取任意不可变对象。来源损坏会显式失败，但评论仍可读。新 continuation 中的相同文本不是同一目标：除非确认精确内联身份，否则应显示 Thread 评论列表和原始输出视图。读取原始输出不会将它选为执行输入。

评论返回 `version`（初始为 1）和可选 `updated_at`。`PATCH .../comments/{comment_id}` 只接受 `{"body":"Revised feedback","expected_version":1}`；身份、目标、选区和署名保持不变。`DELETE .../comments/{comment_id}?expected_version=1` 删除后返回 204。过期编辑或删除返回 `409 comment_version_conflict`。相同编辑重试可核对下一版本；重复删除成功，不会恢复记录。所有可信实例参与者都可编辑或删除；作者名称不是访问控制身份。评论正文不支持共同编辑，也没有回复或解决流程。`POST /api/threads/{root_thread_id}/comments/{comment_id}/capture` 显式将完整评论（正文、选区和署名）及完整原始 assistant 块捕获为 Thread 附件。提供 `?expected_version=<reviewed version>` 可拒绝捕获前的变化。响应使用已有 `ThreadAttachment`，带 `source.kind: comment_reference`、根线程与评论 ID、精确保存目标。新增顶层 `comment` 元数据包含有界作者、正文、引用预览和捕获版本。后续编辑或删除不改变已捕获字节。此操作不需要原生共享或模型侧 Capability。完整 UTF-8 内容（含署名）必须在 64 KiB 内；来源不可用或内容过大会失败，不截断。捕获不修改共享输入区，也不接收执行。客户端只在成功后选择返回的附件 ID。

普通提交和根运行引导将不可变捕获展开为原生文本，保留现有 `harness_ui.attachment` 元数据。客户端可显示可检查的评论引用卡片，无需在编辑器展开文本；模型仍收到完整捕获。保留策略、八个附件上限和仅捕获内容的清除规则不变。单独发布或查看永不成为模型输入。

## 共享输入区

`GET /api/drafts` 以 `{thread_id, draft_id, unsent_since}` 列出非空共享草稿，不返回文本，也不加入编辑者。已选 pending/failed 附件计入非空，空白和闲置附件注册项不计入。时间戳只在空草稿变为非空时变化。`draft` 摘要失效通知标识空/非空成员状态变化的 Thread；收到提示或摘要 reset 后重新读取此索引，再使用现有 Thread 查找 API 获取标题、Project 和归档状态。索引独立于最近对话分页，App 重启后消失。它不是执行队列或持久草稿存储。

`features.shared_drafts` 表示支持 WebUI 使用的内存共享输入协议。连接 `/api/threads/{thread_id}/draft/connect`，使用与原生终端连接相同的首帧认证。只有根 Thread 参与，无需计算机共享。每个参与 Thread 在一个 App 中对应一份内存文档。断开移除在线状态，不移除文档或正在执行的 Run。App 关闭丢弃草稿和在线状态；对话历史仍由既有存储负责。

`x-interactive.draft` 描述 JSON 消息结构。服务器发送 `DraftFrame`，包含 `draft_id`、连接内 `participant_id`、base64 编码的 **Yjs v1 完整状态更新** 和当前参与者状态。文档使用两种根类型：`text`（`Y.Text`，仅纯文本）和 `attachments`（`Y.Map<string>`）。内联映射 key 使用 `inline-<UUID>`，值为同一 Thread 的已有附件 ID，或未完成状态 `pending`/`failed`。文本包含不透明的 `U+FFFC + key + U+FFFC` 身份 token，WebUI 将其渲染为不可拆分的文件名控件。只有当前文本中的 token 选择内联输入；闲置注册项支持撤销，仍受完整 CRDT 状态限制。注册项缺失或未完成状态会阻止客户端提交。Token 不会跨过执行输入边界。原生文件或 diff 捕获使用相同 ID，不使用实时路径或重复字节。客户端按编写文本的顺序展开 token。没有 `inline-` 前缀的旧映射 key 仍按 key 排序，在文本之后选择；新客户端保留此读取兼容性。协作者通过 `GET /api/threads/{thread_id}/attachments/{attachment_id}/metadata` 检查选定引用，得到已有 `ThreadAttachment` 视图，不下载字节。此视图保留 Thread 范围和不可变文件或 diff 来源；移除草稿选择不会删除保留输入文件。服务器使用 pycrdt/Yrs，CRDT 项身份和合并由库负责。

将收到的更新应用于本地 Yjs 兼容副本。发送 `{"kind":"sync","draft_id":"...","update_base64":"..."}`，携副本的**完整** 更新，包括依赖和删除集合，而不是状态向量增量。完整更新让离线、重新加入时的合并及编辑状态重发不依赖服务器传输日志。服务器在发布前原子验证完整合并输入区。`draft_invalid` 保持服务器状态不变；请保留被拒绝的本地内容并显示错误，不要悄悄丢弃选择。限制为编码后的 CRDT 状态 512 KiB、纯文本 256 Ki 个字符、八个已选附件，以及既有附件总计 20 MiB。CRDT 历史计入状态限制；不会自动压缩并改变项身份。

在线状态使用 `{"kind":"presence","draft_id":"...","presence":{"name":"Alice","color":"#112233"}}`。可选 `anchor` 和 `head` 为 base64 编码的 Y 相对位置，不是可能过期的字符偏移量。名称和颜色未经验证，仅用于显示。重连时，只有服务器 `draft_id` 与前一实例匹配，才合并离线编辑状态。身份不同表示此前内存草稿已消失；本地恢复应由用户显式选择，不能自动执行，也不能承诺重启恢复。

发送是客户端操作，没有草稿发送端点：

1. 同步待发送的编辑，并在返回 CRDT 状态中确认。
2. 克隆精确的当前副本；将其编写的文本和当前附件身份展开为普通 `/submit` 的有序 `parts`。
3. 提交确认成功后，只删除捕获副本中可见的文本和旧式选择。将完整删除更新合并回实时副本。保留内联注册项：协作者粘贴的未捕获 token 可能复用 key，并在确认后到达。这样可保留并发插入和替换、新增选择。不要按偏移量清空当前编辑器，也不要替换为空文档。
4. 被拒绝或结果未知时保留草稿。不得自动重试提交。重发 CRDT 编辑更新不等于重发执行请求。

显式根运行引导接受有序 `parts`，附件准备和限制与普通提交相同。图片成为原生图片输入；普通文件、二进制或较大捕获保留可读的 Thread 附件引用。最多 64 KiB 的无 NUL UTF-8 文件、diff 或评论上下文还提供带来源的内联文本。仅附件引导有效，有序部分保留编写顺序和元数据。之后的 Host 编辑不会改变这两种路径捕获的字节或来源署名。

### 有序输入请求体

`POST /api/threads/{thread_id}/submit` 和 `POST /api/operations/{receipt_id}/steer` 要求 `parts`，按编写顺序包含字符串和 `{ "attachment_id": "..." }` 引用：

```json
{
  "parts": [
    "Compare ",
    { "attachment_id": "attachment-first" },
    " with ",
    { "attachment_id": "attachment-second" }
  ]
}
```

先通过已有附件端点暂存字节。引用保留原始 Thread 范围 ID，App 不重复上传。重复 ID 是独立出现的一次附件，重复计入数量和字节限制。请求体最多 1024 个部分，总计 256 Ki 个编写文本字符，并受既有附件限制。空输入被拒绝。根 HTTP 输入使用 `parts`，不接受 `prompt` 和 `attachment_ids` 字段。附件引用标识暂存 Thread 字节，不是 Host 路径或编辑器 token。

原生输入内容携带已有 `source_id` 和 `harness_ui.composer.index` 显示元数据。单个附件可展开为使用相同编写索引的文本和二进制部分；显示时按 source/index 分组，不能只按附件 ID 分组。实时和保留历史视图保留这些元数据。

## 当前路由索引

常用操作可查阅此路由索引；完整路由和字段 schema，包括上文专用路由，请以运行中的 OpenAPI 文档为准。

| 方法与路由                                                          | 用途                                                              |
| ------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `GET /api/status`                                                   | 监听器、API 和 App 状态                                           |
| `GET /api/presence`                                                 | 当前标签页目录和可选同页成员                                      |
| `POST /api/threads/{thread_id}/comments`                            | 发布或核对已保存输出评论                                          |
| `GET /api/threads/{thread_id}/comments`                             | 按游标分页读取根 Thread 评论，可按精确目标筛选                    |
| `GET /api/threads/{thread_id}/comments/{comment_id}`                | 读取限定范围内已提交评论                                          |
| `POST /api/threads/{thread_id}/saved-output`                        | 读取选定或评论保留的原始 assistant 文本，有大小限制               |
| `GET /api/threads/{thread_id}/children/{execution_id}/saved-output` | 读取限定到父线程的已保存子文本块及类型化目标                      |
| `GET /api/catalog`                                                  | 已发现的实现引用，不是已配置选择项                                |
| `GET /api/agents/{agent_id}/tool-proxy`                             | 静态 Agent 默认来源分组                                           |
| `GET /api/auth/accounts/{provider}`                                 | 不含凭据的账号和来源检查（`codex`、`grok`、`copilot`、`chatgpt`） |
| `GET /api/auth/accounts/{provider}/sources`                         | 支持的已保存账号和来源选择，目前为 Copilot                        |
| `PUT /api/auth/accounts/{provider}/selection`                       | 显式选择 `{source, account_id}`，不接受客户端文件路径             |
| `POST /api/auth/accounts/{provider}/models`                         | 显式获取 Copilot 认证目录，仅包含 Chat Completions ID             |
| `DELETE /api/auth/accounts/{provider}`                              | 退出兼容账号，不取消登录流程                                      |
| `POST /api/threads/configuration-preview`                           | 创建选择及各配置维度的来源                                        |
| `GET /api/threads/{thread_id}/configuration`                        | 后续保存选择与实际捕获组合的对比                                  |
| `GET /api/threads/{thread_id}/context-usage`                        | 最近报告的请求占用，不是累计用量                                  |
| `GET /api/threads/{thread_id}/usage`                                | 持久保存的根运行和后代观测用量                                    |
| `GET /api/threads/{thread_id}/work`                                 | 当前任务、笔记和子运行摘要；`include` 加入任务或笔记页            |
| `GET /api/host/files`                                               | 有界本机目录页                                                    |
| `GET /api/host/files/info`                                          | 不读取内容，返回解析后的普通文件元数据及 MIME 提示                |
| `GET /api/host/files/metadata`                                      | 本机条目元数据，不跟随末端符号链接                                |
| `GET /api/host/files/text`                                          | 完整可编辑 UTF-8，或明确的二进制、超大内容类型                    |
| `PUT /api/host/files/text`                                          | 使用已观测修订创建或保存文本                                      |
| `POST /api/host/files/transfers`                                    | 签发仅限一个已审阅文件修订和用途的短期访问链接                    |
| `GET /api/host/files/transfer`                                      | 支持字节范围的受限浏览器播放或下载                                |
| `HEAD /api/host/files/transfer`                                     | 受限文件元数据，无内容正文                                        |
| `PUT /api/host/files/content`                                       | 有大小限制的原始上传，带创建或替换前置条件                        |
| `POST /api/host/files/directories`                                  | 创建一个本机目录                                                  |
| `POST /api/host/files/move`                                         | 将一个已观测条目重命名或移动到不存在的目标                        |
| `POST /api/host/files/delete`                                       | 显式非递归删除或有界递归删除                                      |
| `POST /api/threads/{thread_id}/host-file-captures`                  | 捕获已检查文件字节或行，作为 Thread 输入                          |
| `GET /api/host/git/repository`                                      | 发现本机路径实际所属仓库或 worktree                               |
| `GET /api/host/git/status`                                          | 分页读取暂存区、工作区状态，可包含忽略项                          |
| `GET /api/host/git/diff`                                            | 读取一次 staged、unstaged 或 untracked 比较                       |
| `POST /api/threads/{thread_id}/host-git-captures`                   | 捕获已检查补丁字节或行，作为 Thread 输入                          |
| `GET /api/host/terminals`                                           | 列出 App 管理的本机终端                                           |
| `POST /api/host/terminals`                                          | 创建原生交互式 PTY                                                |
| `GET /api/host/terminals/{terminal_id}`                             | 查看终端会话、输出范围和控制状态                                  |
| `DELETE /api/host/terminals/{terminal_id}`                          | 关闭共享终端会话并移除实时身份                                    |
| `GET /api/setup`                                                    | 当前初始化视图                                                    |
| `POST /api/setup/preview`                                           | 预览初始化选择                                                    |
| `POST /api/setup/apply`                                             | 应用初始化选择                                                    |
| `POST /api/environments/preflight`                                  | 为 Project 路径预检查 Full Control 或 Sandbox profile             |
| `GET /api/auth/keys`                                                | 安全的模型 provider 密钥元数据                                    |
| `PUT /api/auth/keys`                                                | 存储 provider 凭据                                                |
| `DELETE /api/auth/keys/{reference}`                                 | 移除 provider 密钥引用                                            |
| `POST /api/auth/logins`                                             | 启动 provider 登录                                                |
| `GET /api/auth/logins/{session_id}`                                 | 读取 provider 登录进度                                            |
| `DELETE /api/auth/logins/{session_id}`                              | 取消或移除选定登录会话                                            |
| `GET /api/configuration/sources`                                    | 已接受配置源元数据                                                |
| `GET /api/configuration/sources/{relative_path}`                    | 可读取的已接受配置源内容                                          |
| `PUT /api/configuration/sources/{relative_path}`                    | 校验并发布配置源替换                                              |
| `DELETE /api/configuration/sources/{relative_path}`                 | 校验并移除非根配置源                                              |
| `POST /api/configuration/validate`                                  | 校验配置源替换，不发布                                            |
| `PATCH /api/threads/{thread_id}/configuration`                      | 带版本检查的精确配置修改                                          |
| `GET /api/threads/{thread_id}/project-defaults`                     | 预览所选 Project 已配置默认值                                     |
| `POST /api/threads/{thread_id}/project-defaults`                    | 检查版本与摘要后应用已检查默认值                                  |
| `GET /api/threads/{thread_id}/project-environments`                 | 预览完整 Project 环境替换                                         |
| `POST /api/threads/{thread_id}/project-environments`                | 仅应用四个已检查环境维度                                          |
| `GET /api/projects`                                                 | 可用 Project 和创建默认值                                         |
| `GET /api/selectors`                                                | 配置选择选项                                                      |
| `GET /api/threads`                                                  | 查询和分页读取 Thread                                             |
| `GET /api/threads/activity`                                         | 导航活动和待处理摘要                                              |
| `POST /api/threads/lookup`                                          | 按身份读取有界根线程摘要，包含已保存完成标记                      |
| `GET /api/threads/{thread_id}/children`                             | 限定到父线程的子列表或精确查询                                    |
| `POST /api/threads/{thread_id}/children/{execution_id}/steer`       | 将子运行引导文本加入队列                                          |
| `POST /api/threads/{thread_id}/children/{execution_id}/cancel`      | 请求取消子运行                                                    |
| `POST /api/threads`                                                 | 使用可选默认值和标题创建 Thread                                   |
| `GET /api/threads/{thread_id}`                                      | 详情、continuation 和可用操作                                     |
| `GET /api/threads/{thread_id}/transcript`                           | 有界保留对话记录                                                  |
| `PATCH /api/threads/{thread_id}/metadata`                           | 带版本检查的标题或归档修改                                        |
| `POST /api/threads/{thread_id}/attachments`                         | 使用文件名暂存原始字节                                            |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}`          | 下载限定范围内的附件                                              |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}/metadata` | 检查名称、大小、媒体类型和不可变捕获来源                          |
| `POST /api/threads/{thread_id}/submit`                              | 提交有序文本和附件引用                                            |
| `GET /api/threads/{thread_id}/decisions`                            | 精确待处理决策视图                                                |
| `POST /api/threads/{thread_id}/decisions`                           | 回复完整待处理集合                                                |
| `GET /api/operations/{receipt_id}`                                  | 查询精确的进程内操作                                              |
| `POST /api/operations/{receipt_id}/steer`                           | 添加引导文本                                                      |
| `POST /api/operations/{receipt_id}/cancel`                          | 请求取消                                                          |
| `WS /api/realtime/connect`                                          | 多路复用的摘要和聚焦观测通道                                      |

`GET /api/openapi.json`、`/healthz`、`/readyz` 及静态导航和资源，是 schema 未列出的其他边界。只有 App 启用原生共享时，`features.host_files` 才为 true。共享已启用且可找到 Git 可执行文件时，`features.host_git` 为 true。`features.host_terminal` 报告原生 POSIX 终端可用性。`features.shared_drafts` 报告内存共享输入协议支持情况。`features.page_presence` 和 `features.output_comments` 分别报告临时页面在线状态和持久化已保存输出评论，与原生共享独立。

## Skill 目录与引用

`POST /api/threads/skills-preview` 接受 `NewThreadDefaults`，返回 `SkillCatalogView`，不创建 Thread。`GET /api/threads/{thread_id}/skills` 返回空闲 Thread 的下次 Run 目录，或活跃 Run 的固定目录。`POST /api/threads/{thread_id}/skills` 接受 `EnvironmentSelectionPatch`，预览仅适用于本次 Run 的本地根目录选择，不修改 Thread；活跃 Run 仍返回固定目录。条目暴露 `item_id`、`name`、`description`、`source_id` 和 `logical_path`；响应包含 `catalog_id` 和 `context_kind`。

提交和根运行引导接受可选 `skill_references` 数组（最多 512 项），每项包含 `catalog_id`、`item_id` 和 `name`。App 在接收前验证引用。旧目录引用按名称解析；声称属于当前适用目录的引用必须匹配项身份。缺失、歧义或重复引用会拒绝输入。文本部分应保留 `$name`；引用不会展开 Skill 字节或授予权限。省略此字段保持既有输入行为。

## 原生 Git Changes

Git Changes 只读，使用与 Files 相同的计算机共享开关。路径指向服务器或容器的检出目录，不是 Agent 所选 Environment。禁用共享返回 `403 host_git_disabled`，直接 App 调用也一样。缺少可执行文件返回 `503 host_git_unavailable`，不禁用 Files；仓库错误不会被当作干净工作区。

1. 使用 `GET /api/host/git/repository?path=<absolute-native-path>` 发现仓库。文件或目录由 Git 解析到所属 worktree。响应状态为 `state: repository`、`not_repository` 或 `bare`；仓库包含 `root`、`git_dir`、`common_dir`、`head_oid` 和 `branch`。Detached HEAD 没有 branch；尚无提交的分支没有 HEAD 对象 ID。链接 worktree、嵌套仓库和子模块保留自己的身份。
2. 读取 `GET /api/host/git/status?path=...&include_ignored=false`。条目包含仓库相对 `path`、可选 `original_path`、分别记录的 `index_status`/`worktree_status`、`kind` 和可选子模块标志。文件可以同时在两个维度存在变化。Git 可能将忽略目录汇总显示。默认每页 200 项，最多 500，扫描超过 10000 项会被拒绝。后续页需提供 `offset` 和上一页 `expected_revision`，冲突时重新列出。状态修订涵盖状态记录，不涵盖每个文件的字节。
3. 读取 `GET /api/host/git/diff?repository_path=...&path=<relative-file>&comparison=unstaged`。`staged` 比较已观测 HEAD（或空树）与暂存区；`unstaged` 比较暂存区与工作区；`untracked` 显式比较新文件。路径按字面匹配，不是模式；目录或忽略项应在 Files 中选择。响应提供身份、暂存区和 diff 修订，以及 `text`、`binary` 或 `unchanged` 内容类型。只有 text 提供 UTF-8 补丁；冲突保留 Git 的 combined/unmerged 形式。可选 `expected_revision` 拒绝已变化的比较。
4. 使用 `POST /api/threads/{thread_id}/host-git-captures` 捕获。JSON 请求体包含 `repository_path`、相对 `path`、`comparison`、必需 `expected_revision`，以及可选同时提供的 `start_line` 和 `end_line`，表示从 1 开始、包含两端的**补丁** 行。服务器重新计算并检查已查看的 diff，然后暂存。响应与文件捕获使用相同的 `attachment`/`prompt_text` 结构。二进制和 unchanged 预览被拒绝；选择二进制文件字节请使用 Files。

捕获来源元数据包含 `kind: git_diff`、Host 位置、仓库与 Git 目录、选定和原始路径、比较类型、HEAD、暂存区与 diff 修订和范围。通过普通 `/submit` 发送附件 ID；后续编辑、暂存区或分支变化不改变保存的字节和来源。最多 64 KiB 的捕获提供带来源的内联文本，较大补丁保留为附件。引导接受相同附件 ID，保留捕获字节和来源。既有文件来源和普通附件保持兼容。

查询按需获取最新状态，没有 Git 数据库或 watcher。文件修改后、回到 Changes 时，或需要观察其他用户和进程的编辑时，应刷新。Git 状态属于共享检出目录，不代表单次 Run 的修改归属。每个子进程最多 15 秒，stdout 2 MiB，stderr 64 KiB；超限返回 `413 host_git_too_large`，超时返回 `504 host_git_timeout`，取消时回收进程。不会将部分补丁表示为完整补丁。API 禁用外部 diff/textconv 辅助程序和可选暂存区刷新写入，永不暂存、提交、丢弃、切换分支或执行远程操作。

## 原生 Host Files

使用 `a13n-harness-ui webui` 启动。计算机共享默认启用，将服务器操作系统账号的文件权限暴露给获准访问实例的客户端，与 Agent Environment 选择独立。可用 `--no-share-computer` 禁用。Docker 中共享的是容器及其挂载，不是浏览器所在机器。认证绕过不改变共享选择。禁用共享时，原生操作返回 `403 host_files_disabled`；直接 App 调用也会在访问文件系统前拒绝。`/api/projects` 的 Project 根目录是导航起点，不是访问限制目录；Files 也可在 Git 或任何 Project 之外使用。

读取 `GET /api/host/files?path=<absolute-path>` 获取目录，或 `/api/host/files/metadata?path=...` 获取条目元数据。目录默认每页 200 项，最多 500；扫描超过 10000 项会被拒绝，响应包含 `next_offset`。后续页同时传入 `offset` 和上一页 `directory.revision`；冲突时需重新开始列出。元数据描述末端符号链接本身。`/api/host/files/info?path=...` 跟随链接，不读取内容，返回 `entry`、`resolved_path` 和 `media_type`；可选的 `expected_revision` 会拒绝过期观测。MIME 由标准库根据解析后的目标文件名推断，不进行内容解码；未知或带内容编码的类型使用 `application/octet-stream`。前端按 MIME 选择展示组件，不识别或不支持的类型保留文本或下载操作。文本读取和浏览也返回解析后的目标及其修订。

编辑前先读取 `/api/host/files/text?path=...`。`presentation: text` 提供 512 KiB 内完整、无 NUL 的 UTF-8 文本；`binary` 和 `too_large` 不提供可编辑文本。保存使用 `PUT /api/host/files/text`，JSON 包含 `path`、`text` 和观测到的 `expected_revision`。省略修订表示**仅创建**，不是最后写入生效。过期保存返回 `409 host_files_conflict`，客户端缓冲区不应丢弃。保存符号链接需显式选择解析后的目标。原子替换硬链接文件只改变选定目录项，其他别名保留原字节。原始上传使用 `PUT /api/host/files/content?path=...&expected_revision=...`，发送 octet-stream 字节，上限 10 MiB；只有新文件可省略修订。这个端点只接受上传；所有本机文件下载统一使用下面的受限流式传输。

所有本机文件下载和图片、音视频预览通过带认证的 `POST /api/host/files/transfers` 获取受限链接，请求为 `{"path":"/absolute/clip.mp4","expected_revision":"<reviewed>","disposition":"inline"}`（默认值 `attachment` 表示附件下载）。响应包含 `url` 和 Unix 秒数 `expires_at`。签名链接只在当前监听实例的 30 分钟内，授权 GET/HEAD `/api/host/files/transfer` 访问该文件修订和实际 disposition，不包含实例 API key。`Host`/`Origin` 请求头和计算机共享检查仍然生效。下载不区分文件名或整文件大小，始终使用附件 disposition 和 octet-stream 内容类型。inline 请求使用推断的图片（SVG 除外）、音频或视频 MIME，其他类型自动变成附件下载的 octet-stream，不会因为类型不受支持而拒绝传输。能否解码由浏览器决定，失败时仍可下载原文件。直接将该 URL 用作浏览器预览源或附件下载链接，不要先在 JavaScript 中收集整个文件 Blob。传输每次最多读取 256 KiB，不限制整文件大小。单个字节范围返回 206；无法满足的范围返回 416，并带 `Content-Range: bytes */{size}`。HEAD 只返回响应头，不返回正文。不支持的范围单位和多段范围会被忽略；If-Range 不匹配时返回完整表示。发送响应头前会先进行一次有界读取来验证文件。报告大小不可靠的本机普通文件读取到 EOF，不声明 Content-Length 或范围支持。发现修订变化时，若响应头尚未发送则返回 409，否则终止已开始的传输。图片预览的解码上限仍为 10 MiB，独立于下载。修订过期时，应先刷新元数据再重新获取链接。过期或无效的签名链接不能授权访问，监听实例重启后原链接失效。

创建目录接受 `{"path":"/absolute/new-directory"}`，父目录必须存在。移动接受 `path`、`destination` 和源 `expected_revision`，原子拒绝已有目标（包括并发创建），拒绝跨设备移动，不隐式复制再删除。不支持不可覆盖移动的平台或文件系统返回 `host_files_unsupported`，不会冒险覆盖。删除接受 `path`、`expected_revision` 和可选 `recursive: true`；不递归时目录必须为空。递归预检查限制最多 10000 项和 128 层目录。符号链接作为条目删除，不跟随目标。后续 `host_files_partial_failure` 会报告已完成删除；请刷新，不要盲目重试原目录树删除。

原生修订是不透明的操作系统元数据观测值，不是内容哈希或历史版本。读取文件时检查捕获字节期间的变化；保存时在原子替换前重新检查。外部进程仍可能在最终前置条件检查和本机修改之间产生竞争。文件操作不是操作系统范围事务。权限失败返回 `403 host_files_permission_denied`，路径缺失返回 404，操作超限返回 413。请求断开不会放弃已开始的文件系统工作，响应丢失可能意味着修改已完成。任何修改都不会自动重放。

### 选择已检查的文件内容作为输入

`POST /api/threads/{thread_id}/host-file-captures` 接受 `path`、已检查目标的 `expected_revision`，以及可选同时提供的 `start_line` 和 `end_line`（从 1 开始，包含两端）。响应包含普通 `attachment` 及其 `source`（Host 位置、请求和解析后路径、修订与范围），捕获文本在 64 KiB 内时还包含 `prompt_text`。源文件在**选择时** 读取，不是在发送时读取。

在 `/submit` 请求体的 `parts` 中添加 `{"attachment_id": "<returned attachment ID>"}`。小文本捕获将带来源内容内联到模型输入；二进制和较大文本保留为附件，不冒充内联文本。既有附件数量、总输入限制、Thread 范围和临时文件、保留规则仍适用。没有捕获来源信息的普通上传文本附件保持既有行为。

根运行引导接受有序 `parts`；App 按普通提交相同的方式准备图片、普通文件和捕获上下文。选定内容不会被悄悄省略。子运行引导仍只接受 `prompt`。共享编辑由独立的[共享输入协议](#shared-composer)提供。

## 检查配置与工作状态

`POST /api/threads/configuration-preview` 接受 `NewThreadDefaults`，返回解析后的创建选择及各配置维度的 `provenance`。最终来源为 `explicit`、`project`、`agent`、`global` 或 `builtin`；显式 null Project 和空列表保留原有含义。`GET /api/threads/{thread_id}/configuration` 中，`next_run` 包含来源为 `thread` 的已保存选择；这些 ID 不保留历史继承来源。当前值等于默认值，不证明已有 Thread 曾从该来源继承。

检查结果包含当前已接受配置代次、下次 Agent 的 Model 和 Capability ID，以及基于 Thread 所选 MCP、插件 ID 的静态 Tool Proxy 视图。这是配置检查，不是执行就绪检查。`/api/agents/{agent_id}/tool-proxy` 单独显示 **Agent 默认** 成员。`/api/catalog` 列出已发现实现 key，`/api/selectors` 列出已配置可选资源。闲置分组不会激活配置源，两种视图都不会连接 MCP。

`captured` 是实际已发布 Run 组合的白名单视图。`capture_source: active_operation` 指定精确回执和 Run；null 捕获表示该操作尚未发布组合，不表示前一个 Run 捕获适用。没有活跃工作时，`selected_continuation` 读取保存 continuation 的组合，并包含 continuation ID。资源内容或已保存选择的变化不会改变早先捕获。指令、凭据、模型和原生配置载荷、MCP 传输及依赖导入路径均省略，`omitted_fields` 指明这些类别。最多返回 100 个直接子选择摘要，另有 `omitted_children`；它不是可执行配置，也不是递归子图。

`/api/threads/{thread_id}/context-usage` 报告最近保留的根请求占用，不实时计算 token。`/usage` 返回观测到的根、后代及合计用量，保留既有未知成本和省略字段；它不是上下文大小。`/work?include=notes` 在当前工作摘要中加入有界笔记页，数据来自活动 Run 或已保存 continuation。`/api/auth/accounts/{provider}` 返回 `codex`、`grok`、`copilot` 或 `chatgpt` 的不含凭据账号状态。DELETE 通过既有存储退出该账号，返回是否移除条目；它不取消 `/api/auth/logins/{session_id}` 上待处理的登录会话，也不取消正在运行的执行。

## 配置 Project 与 Thread

配置源查询描述**已接受的配置代次**，它可能与磁盘上的无效或刚编辑文件不同。`GET /api/configuration/sources` 列出相对路径、资源 ID、摘要和可编辑状态；按路径 GET 在可用时包含源文本。MCP 源文本不返回（`content: null`、`content_available: false`），因为可能包含明文凭据。不要将此 null 保存为替换内容。账号凭据存储不属于配置源。

使用 `PUT /api/configuration/sources/{relative_path}` 和 `{"content":"..."}` 创建或替换已批准配置源。`POST /api/configuration/validate?path=projects/work.yaml` 接受相同请求体，仅验证，不发布。两者都验证最终完整候选配置，因此替换可修复格式错误源，删除可移除无效且未使用源。其他无效文件仍阻止发布。不能删除根配置，不允许路径穿越、符号链接配置源或任意 Host 路径。

配置源保存采用最后写入生效，**不** 使用预期源摘要。验证不会预留文件。发布响应区分写入的源摘要与后续配置代次摘要；其他编辑者可能随后写入。响应失败或断开不证明文件未写入。重试前检查当前状态和已接受配置源。

预览创建，不分配 Thread：

```bash
curl --fail-with-body "$HUI_URL/api/threads/configuration-preview" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"project_id":null}'
```

此请求体直接为 `NewThreadDefaults`，而创建 Thread 时嵌套在 `defaults` 下。预览从当前配置解析精确的 Agent、Environment、Harness Plugin、Environment Run Extension 和 MCP 选择；创建会再次解析，不预留预览结果。Null Project 屏蔽全局 Project 默认值。`/api/projects` 显示 Project 默认值，Thread 详情显示已有保存选择。

`PATCH /api/threads/{thread_id}/configuration` 接受 `expected_version` 和 `patch`。支持字段为 `project_id`、`agent_id`、`default_model_id`、`local_roots`、`environment_profile_id`、`environment_bindings`、`default_environment`、`harness_plugin_ids`、`environment_run_extension_ids` 和 `mcp_server_ids`。省略保留已保存值，`project_id: null` 清除 Project，空列表表示不选择。修改 Agent 不会隐式替换其他已保存维度。这些根 Thread 命令不修改子 Thread 或已捕获 Run。

仅替换本地根目录、本地 profile、远程绑定和默认环境时，使用 GET/POST `/api/threads/{thread_id}/project-environments`。预览包含最终完整替换，应用时使用返回的 `expected_version` 和 `defaults_digest`。除此之外，Project 编辑不修改已保存环境选择。路径是引用，不会复制文件系统。

要应用所选 Project 更广泛的已配置默认值：

1. GET `/api/threads/{thread_id}/project-defaults`，查看 `current`、`replacement` 和 `patch`。
2. 携返回的 `expected_version` 和 `defaults_digest`，向同一路径 POST。
3. 返回 409 时重新获取预览，不要自动接受新值。

只应用 Project 指定的维度，不应用所有更低优先级的创建默认值。空组合没有应用操作。此操作使用 Thread 并发检查，不改变配置源文件最后写入生效的规则。

## 元数据、决策与附件

元数据 PATCH 接受 `expected_version` 和 `patch`，请使用 Thread 当前 `metadata_version`。省略标题保留原值，null 清除标题。提供 `archived` 时必须为布尔值，归档要求没有活跃根操作。配置版本和 continuation ID 是不同的前置条件。

决策回复携带 `expected_continuation_id`，每个选定请求必须恰好出现一次。问题、审批和外部结果类型必须匹配待处理约定。不能用普通提示词替代暂停 continuation，也不能只提交当前界面方便回答的问题。

附件上传使用原始字节和 `name` 查询参数，不使用 multipart form data。协议声明 `application/octet-stream`；请保留返回的附件 ID，只在所属 Thread 内使用。限制为单个 10 MiB、每次输入八个、合计 20 MiB。下载返回带附件 disposition 的字节，不是 JSON。根运行引导接受与提交相同的有序 `parts`。子运行引导使用纯文本 `SteerRequest`（`prompt`），会拒绝附件字段，不会忽略。

## 导航与查看子工作

- `GET /api/threads/activity` 返回导航摘要、待处理数量、当前活动和保留的终态结果。接受 `project_id`、`query`、`include_archived`、`cursor` 和 `limit`。
- `POST /api/threads/lookup` 接受 `{"thread_ids":["thread-..."]}`，包含 1–100 个 ID，每个 1–80 字符。返回匹配根摘要的 `ThreadPage`，包含归档根线程和不可用 Project 引用，不分页，也不加载 continuation。缺失 ID 和子 Thread 省略，重复 ID 合并。读取不改变元数据、导航顺序或执行。
- 根摘要可包含最近一次成功保存根 Run 的 `completion: {version, run_id, continuation_id, completed_at}`。它在 App 重启后保留，失败、取消和后续检查点也保留。已有数据库初始没有历史标记。对话记录页面携带加载记录时精确 Thread 快照的 `completion_version`，首次标记成功前为零。实现个人已读状态的客户端应确认已渲染版本，不是较新的摘要版本；服务器没有按用户确认端点。
- `GET /api/threads/{thread_id}/work` 返回当前任务、笔记和子运行摘要，数据来自活动 Run 或已保存 continuation。加入 `include=tasks` 或 `include=notes` 获取任务页或笔记页。决策接受 `expected_continuation_id`，不匹配返回冲突，不混合快照。
- `GET /api/threads/{thread_id}/children` 列出该精确父线程下的子运行。提供 `execution_id` 查询指定子运行，或使用 `cursor`、`limit` 分页。
- 向子运行 `/steer` POST `{"prompt":"..."}`，或调用 `/cancel`，只控制所指定父线程下的该执行。控制确认不代表已完成。

轮询以及精确回执的引导、取消，请使用现有根操作 GET。这些操作不引入其他执行协调器或持久工作队列。

## 观测实时通道并恢复缺口

通过 WebSocket 连接 `/api/realtime/connect`，首个 JSON 消息为 `{"api_key":"<instance key>"}`，用于认证。凭据不能放在 URL 中。使用唯一通道 ID 订阅：

```json
{"version":1,"kind":"subscribe","channel":"focused-root","stream":"focus","root_thread_id":"<thread ID>","after":null}
```

摘要提示用 `stream: "summary"`，不带 `root_thread_id`。活跃根保留容量，其余通道（含摘要）共用最多十二个空闲额度。每个根一个通道，摘要也仅一个。取消订阅用 `{"version":1,"kind":"unsubscribe","channel":"focused-root"}`；version-1 ping 回复 `{"version":1,"kind":"pong"}`。消息包含 `version: 1`、通道 ID 和 `frame`，没有 SSE 回退。

观测帧为 JSON 对象。不带 `after` 的聚焦事件流首先收到 `kind: "snapshot"`。存在 `snapshot.root_stream` 时，快照游标为 null，随后发送 `kind: "root_stream"` 批次；每批最多包含该精确 Run 的已有 Stream Protocol observer 中 16 个带索引事件。将它们应用一次到 Run 临时显示，再保存 `kind: "ready"` 的 `resume_cursor`。Ready 前中断时，丢弃未完成初始化，重新开启观测。没有根回放时，初始快照已携游标。后续 `kind: "event"` 帧携带实时事件及其游标。

根事件前缀只包含快照切换时已发布的事件，不包含后续观测。Observer 索引不是实时 hub 全局序列。独立获取保存对话记录，绑定所选 continuation；continuation 前进时替换临时输出。`recent_events` 只是不完整的诊断上下文，不能在历史或根回放旁再次追加。子运行检查使用已有精简的已结束活动视图，重连不暴露尚未结束的子活动。

只在应用帧后保存游标；重连通过新订阅命令的不透明 `after` 字段恢复。有效游标假定客户端保留了显示数据，新加载页面需重新初始化。这不同于 Service Run 事件流的 `Last-Event-ID` 约定。

摘要先发送 `kind: "open"`，再发送 `kind: "invalidation"`，需重新读取受影响资源。`resumed: true` 回放错过提示，false 需初始核对。用 `POST /api/threads/activity/lookup` 批量查询最多 100 个待更新 ID。只有活动首页包含活跃工作。终态 `root_operation` 可携带 `notice: {receipt_id, status, brief}`，状态为 completed/failed/suspended，预览最多 320 字符。通知在续接选择后尽力投递，按 epoch 和回执去重；新订阅或 reset 不通知历史完成。聚焦与摘要游标不同，绑定范围/epoch，稀疏序列有效。

`kind: "reset"` 要求重新获取数据并建立新订阅。实时缓冲区有上限，仅在进程内保存。`watch_thread` 提供先订阅后查询的切换，不提供事务式持久回放。断开停止观测，不停止执行。每个通道独立恢复；根 Run 切换只替换对应聚焦通道，不替换 socket 或无关观测。

## 错误与版本

错误使用 `{"error":{"code":"...","message":"..."}}`：400 表示 App 请求无效，409 表示冲突/旧版本/预检查，413 表示请求体超限，404 表示资源/回执不可用，503 表示 App 未就绪/停止中。查询验证使用相同格式，状态 422、码 `request_invalid`。认证、Origin 和 Host 拒绝分别为 401、403、400。

根据错误码和当前状态处理，不要匹配文本。超时或客户端断开不能确定修改是否生效。假定源码文档匹配已部署监听器前，请检查 `/api/status` 和 schema 兼容性。
