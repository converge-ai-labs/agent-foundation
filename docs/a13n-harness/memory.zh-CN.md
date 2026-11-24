---
title: 记忆
description: 为 Harness 智能体挂载带版本校验的文件记忆或语义化 Mem0 记录存储，使知识与用户偏好跨线程持久保留。
---

当一个线程沉淀的经验需要供后续其他线程复用时，使用**记忆**；仅限单个线程内的任务列表与笔记请使用[工作状态](context.md#working-state)。

| 记忆类型 | 能力                     | 模型读写方式                                                  |
| -------- | ------------------------ | ------------------------------------------------------------- |
| 文件记忆 | `FileMemoryCapability`   | UTF-8 Markdown 目录树（`DirectoryFileStore`），支持乐观锁校验 |
| 记录记忆 | `RecordMemoryCapability` | 按语义相似度自动召回的短事实（`MEM0_OSS` / `MEM0_PLATFORM`）  |

## 文件记忆

使用 `DirectoryFileStore` 与 `FileMount` 挂载持久化目录，并将每个线程的 `MemoryCursors` 快照与 `HarnessState` 一并保存，避免每轮重复注入未变更的文件：

```python title="memory_demo.py"
import asyncio
from tempfile import TemporaryDirectory
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMemoryLimits, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = DirectoryFileStore(directory)
        cursors = MemoryCursors()
        memory = FileMemoryCapability(
            [FileMount("user", store, "write", always_load=("preferences.md",))],
            limits=FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096),
            cursors=cursors,
        )
        executable = HarnessBuilder().build(
            AgentSpec(model="openai-responses:gpt-5"),
            output_type=str,
            capabilities=(memory,),
        )
        result = await executable.run("Remember that I prefer concise Chinese replies.")
        print(result.output_or_raise())
        # 同时持久化 result.state.model_dump_json() 与 cursors.snapshot()
```

### 模型获得的内容

每个 `FileMount` 会注入 `<memory-context>` 目录索引（及 `always_load` 文件内容），并暴露以下 `memory_file_*` 工具（`read` 挂载仅暴露 `view` 与 `grep`）：

| 工具                 | 作用                                                  |
| -------------------- | ----------------------------------------------------- |
| `memory_file_view`   | 列出目录或读取文件内容，并记录已观测版本              |
| `memory_file_grep`   | 跨文件搜索匹配行（可选 `regex=True`）                 |
| `memory_file_create` | 创建新文件（支持 YAML `description` 前置元数据）      |
| `memory_file_edit`   | 将 `old_string` 替换为 `new_string`（校验已观测版本） |
| `memory_file_append` | 向已有文件末尾追加内容                                |
| `memory_file_move`   | 重命名或移动文件                                      |
| `memory_file_delete` | 按已观测版本删除文件                                  |

若其他线程并发修改了当前线程已观测的文件，写入会返回 `read_conflict`，提示模型重新读取后再编辑。如需对接自定义数据库，可实现 `a13n_harness.providers.memory.FileStore`。

## 记录记忆

将 Mem0 OSS 或 Mem0 Platform 存储绑定到租户/用户 `namespace`，并挂载 `RecordMemoryCapability`：

```python
import httpx2
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMemoryLimits, RecordMount
from a13n_harness.providers.memory import MEM0_OSS  # 或 MEM0_PLATFORM

async with (
    httpx2.AsyncClient(timeout=30) as http,
    MEM0_OSS.open({"base_url": "http://localhost:8888"}, namespace="alice", http=http) as store,
):
    memory = RecordMemoryCapability(
        [RecordMount("facts", store, "write")],
        limits=RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192),
    )
    executable = HarnessBuilder().build(
        AgentSpec(model="openai-responses:gpt-5"), output_type=str, capabilities=(memory,)
    )
    await executable.run("Remember that I drink green tea.")
```

每次 Run 启动时，`<memory-recall>` 会自动注入最相关的记录（设置 `recall=False` 可关闭自动召回）。模型通过 `memory_record_search`、`memory_record_list`、`memory_record_add`、`memory_record_update` 与 `memory_record_delete` 检索和更新记录（`read` 挂载仅开放检索与列表）。自定义向量后端可实现 `RecordStore`。
