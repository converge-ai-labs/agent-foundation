---
title: 独立记忆参考
description: 创建文件型或记录型记忆，挂载到线程并检查保存的知识。
---

需要把独立文件存储或 mem0 记录后端直接挂载到线程时，使用独立记忆。共享知识、会话笔记和自动整理从[记忆空间](memory.md)开始。记忆工具需要工作空间权限与选中的挂载，不需要计算环境。

## 独立记忆

独立记忆以工具可见名称直接挂载到线程：

- **文件型记忆**在 Service 的 PostgreSQL 数据库中保存文本文件树和修改历史。适合人和 agent 都需要查看、编辑的结构化知识。编写方式见[文件型记忆](../a13n-harness/memory.md)。
- **记录型记忆**在 mem0 后端保存简短事实。运行开始时，语义搜索召回与输入相关的记录。更新采用最后一次写入生效的方式，没有文件版本。

创建存储并挂载到线程后，请 agent 保存并读回一条简短事实。每次运行接收时固定挂载，挂载修改影响后续运行。删除整个记忆会删除其文件和历史，因此应先另行备份。

## 创建文件型记忆

使用工作空间 `write` 权限创建存储：

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Team conventions", "type": "postgres",
       "guide": "Keep one file per topic. Record decisions with their reason.",
       "always_load": ["README.md"]}'
```

保存返回的 `id`（`mem_...`），然后[挂载到线程](standalone-memory.md#mount-a-memory-on-a-thread)。`postgres` 是默认类型；记忆使用 ID，而非 key。

`guide` 告诉 agent 保存什么、如何组织。省略或设为 `null` 时继承部署为该记忆类别设置的指南；`""` 表示不提供指南。`inherited_guide` 展示继承的文本。`always_load` 最多选择 64 个路径，在线程首次看到记忆时，优先在上下文预算内加载。这些路径可以稍后创建。只有拥有 `write` 的主体能修改选择。

`PATCH /memories/{id}` 使用记忆的 `If-Match` 修改名称、描述、标签、指南和始终加载的路径。修改应用于后续尝试，包括已有运行的后续尝试。`GET /memories` 按 `label`、`kind` 和 `type` 筛选；文件型记忆提供文件数、内容量和历史量统计。

`DELETE /memories/{id}` 需要 `write` 和 `If-Match`，会删除文件、历史和线程挂载。正在使用该记忆的 agent 在下一次记忆工具调用时收到 `memory_deleted`。

## 记录型记忆

### 配置记忆 provider

[记忆 provider](resources.md#providers) 将工作空间连接到记录后端。通过 `/memory-providers` 添加，然后先测试连接，再创建记忆。

托管 mem0 Platform 使用 `mem0_platform`，其 `base_url` 默认为 `https://api.mem0.ai`：

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_platform", "name": "mem0", "config": {}, "credential": {"api_key": "m0-..."}}'
```

使用[自托管 mem0 REST 服务器](https://docs.mem0.ai/open-source/features/rest-api)时，先配置其模型、嵌入模型和向量存储，再注册地址：

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_oss", "name": "mem0",
       "config": {"base_url": "https://mem0.internal.example.com"}, "credential": {"api_key": "..."}}'
```

自托管凭据可选，通过 `X-API-Key` 发送；删除命名空间需要服务器管理员密钥。明文 HTTP 需要在[出站策略](configuration.md#outbound-requests)中显式允许其 origin。自托管服务器最多列出每份记忆的 1000 条记录。

调用 `POST /memory-providers/{provider_id}/test` 检查地址和凭据。测试从未使用的命名空间读取一页数据，不修改记录。

### 创建记录型记忆

使用已启用 provider 的 ID 和匹配的类型：

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "User facts", "type": "mem0_platform", "provider_id": "memprov_...",
       "guide": "Record one stable fact about the user per record."}'
```

每份记忆拥有后端中的一个**命名空间**，即 mem0 的 `user_id`。Service 默认生成 `a13n-` 加从记忆 ID 派生的 32 位十六进制字符。提供 `namespace` 可接管已有记录。它必须包含 1–256 个可打印字符，不含空白或 `*`。命名空间已归另一份记忆所有时返回 `409 already_exists`。

类型、provider 和命名空间保持不变。`always_load` 只适用于文件型记忆。删除记录型记忆会安排后台命名空间清理；完成前以 `namespace_purging` 阻止复用。如果后端持续拒绝，重试耗尽后记录仍可能保留；参阅 [outbox 策略](operations.md#outbox-retention-and-capacity)。

### 读取与编辑记录

读取记录需要 `read`；添加、替换或删除需要 `run`：

| 请求                                        | 结果                                              |
| ------------------------------------------- | ------------------------------------------------- |
| `GET /memories/{id}/records?limit=&cursor=` | 按后端顺序返回记录，附带 `next_cursor`            |
| `POST /memories/{id}/records/search`        | 返回与 `{query}` 最接近的记录及分数，可选 `limit` |
| `POST /memories/{id}/records`               | 添加 `{text}`                                     |
| `PUT /memories/{id}/records/{record_id}`    | 用 `{text}` 替换完整文本                          |
| `DELETE /memories/{id}/records/{record_id}` | 删除记录                                          |

记录包含 1 到 `memory.record_chars` 个字符（默认上限 8000），没有 ETag。遇到原因是 `write_unconfirmed` 的 `409 conflict` 后，先列出或搜索再重试：后端可能已经写入。后端不可用时返回 `503 unavailable`，provider 已禁用时返回 `422 disabled`。审计记录 ID，不包含文本。

在 Console 中选择**查看来源**，检查可选的 `provenance`：已知创建来源、最近一次平台确认的写入、操作身份，以及有权限访问的运行链接。技术详情包含 Attempt 和工具调用 ID。导入的记录可能没有创建来源。来源说明写入归属，不是完整版本历史或用户原话。

来源索引与 provider 写入分开；索引失败不会让已确认的写入失败或自动重试。直接在后端进行的编辑可能未被记录。当前文本与最近记录的写入不一致时，Console 会提示；文本一致本身不能证明没有发生外部修改。

### 召回

运行开始时，每份启用 `recall` 的已挂载记录型记忆用运行的首条输入进行搜索。默认每份记忆召回五条、最多 8 KiB、搜索限时两秒，分别由 `memory.recall_limit`、`memory.recall_bytes` 和 `memory.recall_seconds` 控制。结果作为不可信数据进入上下文。失败或过慢的搜索会被跳过，让运行继续。同一运行的后续输入不重复召回。

## 在线程上挂载记忆

从 `GET /threads/{id}/memories` 获取线程当前 ETag。将 `THREAD` 设为线程 ID，将 `THREAD_ETAG` 设为包含引号的 ETag。把 `mem_...` 替换为独立记忆 ID：

```sh
curl -X POST "$A13N_URL/api/v1/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

启动后续运行，请 agent 在 `team` 记忆中保存一条约定，再将其读回。检查[文件与历史](standalone-memory.md#files-and-history)或[记录](standalone-memory.md#read-and-edit-records)，确认实际保存结果。

名称遵循 `^[a-z][a-z0-9-]{0,62}$`。每个线程只能使用每个名称和记忆一次。`read` 权限提供读取与搜索工具；`write` 增加修改工具。记录的 `recall` 默认 `true`，文件忽略此设置。默认最多八个挂载（`memory.mounts_per_thread`）。

挂载修改需要 `run` 和**线程**的 `If-Match`，并返回新线程 ETag。`PATCH /threads/{id}/memories/{name}` 修改权限或召回设置；`DELETE` 移除挂载。替换记忆时，先移除挂载再添加。创建线程和分叉的请求接受 `memories`；分叉复制源挂载。归档会移除线程挂载。

要设置 agent 默认值，将 `config.memory_mounts` 设为 `[{name, memory_id, access, recall}]`。线程首次接收运行时，默认值添加尚未使用的名称和记忆。此后保留线程自身的修改，包括移除操作。默认记忆已删除时，首次接收以 `invalid_argument` 失败；超过限制时以 `memory_mount_limit` 失败。

异步[子 agent](agent-configuration.md#subagents) 的线程先继承父运行挂载，再添加自身 agent 默认值。内联子 agent 不接收记忆上下文或工具。

## Agent 获得的内容

`memory` [工具集](tools.md#built-in-toolsets)默认启用。已挂载文件型记忆使用 Harness [文件工具](../a13n-harness/memory.md#what-the-model-gets)。记录型记忆使用 `memory_record_search`、`memory_record_list`、`memory_record_add`、`memory_record_update` 和 `memory_record_delete`。Tool key 使用 `file_*` 或 `record_*`；permission ID 使用 `memory.file.*` 或 `memory.record.*`。禁用一个工具会从所有挂载中移除它；禁用工具集后，自动上下文和召回仍然可用。

文件型记忆首次提供始终加载的文件和文件索引。后续运行接收变化的文件，包括其他线程和人的编辑。压缩历史后，线程再次获得完整上下文。上下文受 `memory.context_bytes`（默认 32 KiB）和 `memory.always_load_bytes` 限制。被截断或省略的文件需要显式调用 `memory_file_view`。连最小块也放不下时，运行会收到读取与清理提示。这些有界结果仍推进上下文游标，因此未变化的省略内容不会自动重发。

每次工具调用检查当前工作空间权限：读取与搜索需要 `read`，修改需要 `run`。拒绝调用返回 `forbidden`；provider 已禁用时，记录工具返回 `unavailable`。失败的文件调用保持存储不变。Worker 在写入后、保存检查点前停止时，恢复的尝试可能报告结果未知。重复写入前先读取文件或搜索记录。

## 文件与历史

纠正已保存文件时，先读取当前内容和 ETag，再编辑，然后检查新修订。读取需要 `read`；编辑和恢复需要 `run`；清理历史需要 `write`。`{path}` 是文件路径，例如 `prefs/language.md`。

| 请求                                          | 结果                                                             |
| --------------------------------------------- | ---------------------------------------------------------------- |
| `GET /memories/{id}/files?prefix=`            | 列出以 `/` 结尾的目录前缀下的路径                                |
| `GET /memories/{id}/files/{path}`             | 读取内容和文件 ETag                                              |
| `POST /memories/{id}/files`                   | 在空闲路径创建 `{path, content}`                                 |
| `PUT /memories/{id}/files/{path}`             | 使用文件的 `If-Match` 替换内容                                   |
| `POST /memories/{id}/files/move`              | 使用源文件的 `If-Match`，将 `{source, destination}` 移至空闲路径 |
| `DELETE /memories/{id}/files/{path}`          | 使用文件的 `If-Match` 删除                                       |
| `GET /memories/{id}/revisions`                | 按从新到旧列出修改，可按 `path` 或 `run_id` 筛选                 |
| `GET /memories/{id}/revisions/{seq}`          | 读取修改前内容、结果内容和 diff hunks                            |
| `POST /memories/{id}/revisions/{seq}/restore` | 将该修订替换前的内容恢复为新修改                                 |
| `DELETE /memories/{id}/revisions?path=`       | 移除一个路径的保留历史，留下文件                                 |

路径上已有文件时，恢复使用当前文件的 `If-Match`。旧文件 ETag 返回 `412 precondition_failed`；再次编辑前先重读。修订记录 `principal_id`，agent 写入还记录 `run_id` 和 `tool_call_id`。

默认每文件最多 64 KiB，保留十个修订，每份记忆的内容与历史合计最多 32 MiB。先清理旧历史；内容本身超限时返回 `409 conflict`，原因为 `memory_full`。限制与指南预算见[记忆设置](configuration-reference.md#memory)。
