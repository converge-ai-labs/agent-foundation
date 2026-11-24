---
title: 独立记忆参考
description: 创建独立的版本化文件记忆与 Mem0 语义记录记忆，直接挂载到 Service 线程并管理历史修订版本。
---

当不需要跨团队共享的[记忆空间（Memory Spaces）](memory.md)时，可创建独立的**文件记忆**（基于 PostgreSQL 的版本化 Markdown 文件树）或**记录记忆**（基于 Mem0 的语义向量记录），并直接挂载到线程上。

## 创建文件记忆

具备工作区 `write` 权限时，创建 PostgreSQL 文件记忆存储（`guidance.system_preset` 支持 `"general"` 或 `"journal"`）：

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Team conventions", "type": "postgres",
       "guidance": {"system_preset": "general", "custom": "Record decisions with their reason.", "timezone": "UTC"},
       "always_load": ["README.md"]}'
```

携带 `If-Match` 调用 `PATCH /api/v1/memories/{id}` 可修改元数据或 `always_load`，调用 `DELETE /api/v1/memories/{id}` 可删除存储。

### 文件与历史版本

直接查看、编辑或回滚版本化文件（单文件上限 64 KiB，单记忆总上限 32 MiB）：

| 端点                                                  | 操作                                                 |
| ----------------------------------------------------- | ---------------------------------------------------- |
| `GET /api/v1/memories/{id}/files?prefix=` / `/{path}` | 列出目录路径，或读取文件内容与文件 ETag              |
| `POST /api/v1/memories/{id}/files`                    | 在空闲路径创建 `{path, content}`                     |
| `PUT` / `DELETE /api/v1/memories/{id}/files/{path}`   | 携带文件 `If-Match` 替换内容或删除文件               |
| `POST /api/v1/memories/{id}/files/move`               | 携带源文件 `If-Match` 重命名 `{source, destination}` |
| `GET /api/v1/memories/{id}/revisions` / `/{seq}`      | 查看变更历史与 Diff 片段                             |
| `POST /api/v1/memories/{id}/revisions/{seq}/restore`  | 将指定历史版本恢复为一次新提交                       |

## 记录记忆

1. **创建记忆提供方：** 通过 `POST /api/v1/memory-providers` 注册托管 `mem0_platform`（`https://api.mem0.ai`）或自建 `mem0_oss`（`config.base_url`），并调用 `POST /api/v1/memory-providers/{provider_id}/test` 验证连通性。
2. **创建记录记忆：**

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "User facts", "type": "mem0_platform", "provider_id": "memprov_...",
       "guide": "Record one stable fact about the user per record."}'
```

### 读取与编辑记录

运行启动时若启用了 `recall`（默认 `true`），会自动检索并注入最相关的前若干条记录（`memory.recall_limit`，默认 5 条）。可通过 `/api/v1/memories/{id}/records` 直接管理记录：`GET`（分页列表）、`POST .../search`（`{query}`）、`POST`（`{text}`）、`PUT .../{record_id}`（`{text}`）与 `DELETE .../{record_id}`。

## 在线程上挂载记忆

携带**线程**的 `If-Match` 在单个线程上挂载最多 8 个独立记忆（也可在智能体 `config.memory_mounts` 中设置默认挂载）：

```sh
curl -X POST "$A13N_URL/api/v1/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

启用 `memory` [内置工具集](tools.md#built-in-toolsets)后，文件记忆暴露 `memory_file_*`（[文件工具](../a13n-harness/memory.md#what-the-model-gets)），记录记忆暴露 `memory_record_*`（`read` 权限挂载仅暴露读取与检索工具）。
