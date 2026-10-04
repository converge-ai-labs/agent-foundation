---
title: 记忆
description: 通过文件或记录，让 Agent 的记忆跨越单个 Thread。
---

记忆让 Agent 拥有跨越单个 Thread 的知识，例如偏好、决策、约定和事实。多个 Thread 可以共享同一份记忆。记忆分为两种：

|              | 文件记忆                                   | 记录记忆                                                             |
| ------------ | ------------------------------------------ | -------------------------------------------------------------------- |
| 基本单位     | 带路径的文本文件                           | 带 ID 的短记录                                                       |
| 模型如何查找 | Run 开始时读取索引，随后使用 view 和 grep  | Run 开始时召回，随后按相似度搜索                                     |
| 写入方式     | 每次写入都对照当前文件检查前提条件         | 后写覆盖前写                                                         |
| 存储实现     | `DirectoryFileStore`，或自定义 `FileStore` | 通过 `MEM0_PLATFORM` 或 `MEM0_OSS` 使用 mem0，或自定义 `RecordStore` |

只属于单个 Thread 的任务和笔记，请使用[工作状态](context.md#working-state)。

## 文件记忆

文件记忆保存一棵小型文本文件树。每次写入执行时，都会对照当前文件检查前提条件。因此，基于旧内容的修改会失败，不会覆盖另一个 Thread 的工作。

### 挂载本地记忆

`DirectoryFileStore` 将记忆作为普通文件保存在一个目录下。`FileMemoryCapability` 为它指定挂载名称：

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def main() -> None:
    store = DirectoryFileStore(".memory/user")
    cursors = MemoryCursors()
    memory = FileMemoryCapability(
        [FileMount("user", store, "write", always_load=("README.md",))],
        cursors=cursors,
    )
    executable = HarnessBuilder().build(
        AgentSpec(model="openai-responses:gpt-5"),
        output_type=str,
        capabilities=(memory,),
    )

    first = await executable.run("I prefer answers in Chinese. Remember that.")
    print(first.output_or_raise())
    second = await executable.run("What do you know about me?", previous_state=first.state)
    print(second.output_or_raise())


asyncio.run(main())
```

模型通过挂载名 `user` 访问记忆，不会看到实际目录路径。

### 模型获得的内容

- **指令。** 每个挂载点都列出名称、访问权限和指引。`guide=None` 使用 `DEFAULT_FILE_GUIDE`，简要说明应保存什么、如何组织；也可传入自定义文字，或用 `""` 不提供指引。
- **工具。** 只有 `memory_file_*` 工具能够读取和修改记忆。Shell 和 Environment 文件工具无法访问。
- **上下文。** 每次 Run 开始时，每份记忆对应一个 `<memory-context>` 块，展示 `always_load` 文件和索引，索引中每个文件占一行 `path: description`。使用游标时，后续 Run 只收到变化的路径和有变化的 `always_load` 文件的新内容；没有变化时不添加块。

| 工具                 | 功能                                             | 失败条件                           |
| -------------------- | ------------------------------------------------ | ---------------------------------- |
| `memory_file_view`   | 列出目录，或读取文件及其版本                     | 路径不存在                         |
| `memory_file_grep`   | 查找包含指定文字的行，默认按字面匹配并忽略大小写 | 使用 `regex=True` 时正则表达式无效 |
| `memory_file_create` | 创建新文件                                       | 路径已存在                         |
| `memory_file_edit`   | 将 `old_string` 替换为 `new_string`              | `old_string` 不是恰好出现一次      |
| `memory_file_append` | 在文件末尾追加文字                               | 文件不存在                         |
| `memory_file_move`   | 重命名文件                                       | 源文件不存在，或目标已存在         |
| `memory_file_delete` | 删除模型此前查看的版本                           | 文件已变化或已不存在               |

调用因前提条件或版本检查失败时，不会修改任何内容，并返回当前文件，让模型重新读取并判断。例如，两个 Thread 都查看了包含“喜欢茶”和“用英语回答”的文件。一个 Thread 将“用英语回答”改为“用中文回答”。另一个 Thread 随后将“喜欢茶”改为“喜欢咖啡”。两次修改都会保留。之后任一 Thread 再尝试修改“用英语回答”都会失败，并返回文件当前内容。

`read` 挂载只提供 `memory_file_view` 和 `memory_file_grep`。`tools=("view", "grep")` 会缩小所有挂载的工具范围。工具权限规则可以指定从 `memory.file.view` 到 `memory.file.delete` 的工具 ID。

### 编写合适的记忆文件

文件必须是 UTF-8 文本，默认最大 64 KiB。索引行优先使用 frontmatter 中的 `description`，否则使用第一个非空行：

```markdown
---
description: Language and tone preferences
---
- Reply in Chinese.
- Keep answers short.
```

路径是相对路径，例如 `prefs/language.md`，目录隐式存在。`always_load` 指定的文件，其完整内容位于该记忆完整上下文的开头；使用游标时，后续 Run 只在这些文件变化后再次收到它们。只有构建挂载的代码能选择这些文件，因此 Thread 不能将自己写入的内容固定到所有后续 Thread 中。

### 在多次 Run 之间控制上下文大小

`MemoryCursors` 为每份记忆记录最近一次送达该 Thread 的上下文所对应的存储游标。复用同一组游标时，后续 Run 只收到上次送达上下文之后的变化，没有变化则不添加内容。将 `cursors.snapshot()` 与该 Thread 的 `HarnessState` 一起持久化，构建下次 Run 的 Capability 时传入 `MemoryCursors(saved)`。不使用游标时，每次 Run 都会收到完整上下文。自动压缩或 handoff 替换历史后，Capability 会清空游标，使下次 Run 重新获取完整上下文。

`DirectoryFileStore` 没有变更日志。记忆发生任何变化后，下次 Run 收到的都是完整上下文，而非变更列表。

同一次 Run 的所有记忆上下文共用一个预算：

```python
from a13n_harness.capabilities import FileMemoryLimits

limits = FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096, write_retries=3)
memory = FileMemoryCapability([FileMount("user", store, "write")], limits=limits, cursors=cursors)
```

先放入 `always_load` 文件，再由索引共用剩余额度。较大的索引会将目录合并成 `archive/ (37 files)` 这样的行；仍放不下时会截断，并提示通过 `memory_file_view` 查看。

### 在多个进程之间共享记忆

多个进程可以挂载同一目录。`DirectoryFileStore` 使用 `.a13n-memory/` 下的锁文件串行处理每次修改，这些文件不会被列出。游标属于具体 Thread，每个 Thread 应有自己的 `MemoryCursors`，并与其 `HarnessState` 保存在一起。目录存储不保留历史，请自行备份目录，或实现保留历史的 `FileStore`，就像 Service 的文件记忆存储那样。

### 使用自己的文件存储

存储实现 `a13n_harness.providers.memory` 的 `FileStore` 协议：`list`、`read`、采用 compare-and-swap 的 `write`、`move`、`delete`、供上下文游标使用的 `changes`，以及 `purge`。增加 `search` 可实现 `SearchableFileStore`；否则 `memory_file_grep` 会直接读取文件。`validate_path()` 和 `describe()` 应用通用文件规则。对于保留历史的存储，`Origin` 会说明每次变更来自哪个 Run、principal 和工具调用。

[文件记忆规范](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21-file-memory.md)定义了完整存储约定、上下文预算和失败码。

## 记录记忆

记录记忆保存“偏好绿茶”这样的短记录，并在每次 Run 开始时召回与输入含义最接近的内容。记录没有版本，后写覆盖前写。

### 挂载 mem0

`MEM0_OSS` 将自托管 [mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api) 的一个命名空间作为 `RecordStore` 打开，`RecordMemoryCapability` 为它指定挂载名称：

```python
import asyncio

import httpx2

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMount
from a13n_harness.providers.memory import MEM0_OSS


async def main() -> None:
    async with (
        httpx2.AsyncClient(timeout=30) as http,
        MEM0_OSS.open({"base_url": "http://localhost:8888"}, namespace="alice", http=http) as store,
    ):
        memory = RecordMemoryCapability([RecordMount("facts", store, "write")])
        executable = HarnessBuilder().build(
            AgentSpec(model="openai-responses:gpt-5"),
            output_type=str,
            capabilities=(memory,),
        )

        first = await executable.run("I drink green tea. Remember that.")
        print(first.output_or_raise())
        # A new Thread recalls the record.
        second = await executable.run("What should I order at the cafe?")
        print(second.output_or_raise())


asyncio.run(main())
```

命名空间对应保存这份记忆记录的 mem0 `user_id`，每份记忆应使用独立值。如果不提供 `http`，存储会创建自己的客户端，但它只能访问公开 HTTPS endpoint；连接本地服务器需要自己提供客户端。托管 Platform 接受 API key，默认地址为 `https://api.mem0.ai`：

```python
import os

from a13n_harness.providers.memory import MEM0_PLATFORM

async with MEM0_PLATFORM.open({}, {"api_key": os.environ["MEM0_API_KEY"]}, namespace="alice") as store:
    ...
```

两者都原样添加记录，通过回读确认每次写入，绝不会显示或修改其他命名空间的记录。

### 模型从记录中获得的内容

- **指令。** 每个挂载都列出名称、访问权限和指引。`guide=None` 使用 `DEFAULT_RECORD_GUIDE`；也可传入自定义文字，或用 `""` 不提供指引。
- **召回。** 每次 Run 开始时，每份记忆对应一个 `<memory-recall>` 块，显示与输入文字最接近的记录。挂载设置 `recall=False` 可关闭。召回失败或耗时超过 `recall_seconds` 时会跳过，Run 继续执行。
- **工具。** `memory_record_*` 工具用于搜索、列出和修改记录。

| 工具                   | 功能                       | 失败条件                         |
| ---------------------- | -------------------------- | -------------------------------- |
| `memory_record_search` | 查找与查询含义最接近的记录 | 存储不可用                       |
| `memory_record_list`   | 分页列出记录               | 游标不是存储返回的 `next_cursor` |
| `memory_record_add`    | 添加记录                   | 文字为空，或超过 `record_chars`  |
| `memory_record_update` | 替换记录的完整文字         | 此记忆中没有该记录               |
| `memory_record_delete` | 删除记录                   | 此记忆中没有该记录               |

存储无法确认写入时，返回 `write_unconfirmed`：操作可能已经发生，也可能没有，因此工具说明会要求模型先搜索再重新写入。`read` 挂载只提供搜索和列表；`tools=("search", "add")` 缩小所有挂载的工具范围。工具权限规则可以指定从 `memory.record.search` 到 `memory.record.delete` 的工具 ID。

`RecordMemoryLimits` 设置记录大小和每次 Run 的召回限制：

```python
from a13n_harness.capabilities import RecordMemoryLimits

limits = RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192, recall_seconds=2.0)
memory = RecordMemoryCapability([RecordMount("facts", store, "write")], limits=limits)
```

每个召回块最多包含该记忆的 `recall_limit` 条记录，在 `recall_bytes` 内优先保留最接近的记录。

### 使用自己的记录存储

存储实现 `a13n_harness.providers.memory` 的 `RecordStore` 协议：`search`、带游标的 `list`、`add`、`update`、`delete` 和 `purge`，全部限定于一个命名空间。`validate_record_text()` 应用通用文本规则，`MemoryStoreError` 携带工具报告的失败码。

[记录记忆规范](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21a-record-memory.md)定义了完整存储约定、mem0 规则和召回行为。
