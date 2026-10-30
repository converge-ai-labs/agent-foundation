---
title: 记忆
description: 跨 Thread 保存偏好，协调共享写入，并接入文件或记录存储。
---

当一次对话中的知识应该用于后续对话时，就使用记忆，例如用户偏好的语言、项目约定或需要长期保留的决定。Host 决定哪些 Thread 共享存储，以及哪些 Thread 可以修改它。单个 Thread 的任务和笔记使用[工作状态](context.md#working-state)。

根据 Agent 查找信息的方式选择记忆类型：

| 需求                                      | 选择     | 模型如何读取                             | 写入方式                               |
| ----------------------------------------- | -------- | ---------------------------------------- | -------------------------------------- |
| 按 `prefs/language.md` 这样的路径组织笔记 | 文件记忆 | 先看有限大小的索引，再读取文件或搜索文字 | 通过操作条件和已观察的版本保护共享修改 |
| 根据含义查找简短事实                      | 记录记忆 | 自动召回，再按相似度搜索                 | 每条记录有 ID，后写覆盖前写            |

先运行下面的本地文件示例。它需要[快速开始](getting-started.md)中的 Python 环境，无需 Model 凭据或记忆服务器。后面的 mem0 示例则需要可用的后端和已配置的 Model。

## 文件记忆

文件记忆是一棵小型 UTF-8 文本文件树。`DirectoryFileStore` 将它保存在磁盘上，`FileMemoryCapability` 让模型通过名称访问它。存储的生命周期独立于单次 Run，多个 Thread 可以挂载同一份存储。

### 挂载本地记忆

这个程序用确定性 Model 展示两个步骤：第一个 Thread 调用 `memory_file_create`，新的只读 Thread 在起始上下文中收到该文件。将它保存为 `memory_demo.py`：

```python title="memory_demo.py"
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def write_preference(messages, info):
    receipts = [
        part for message in messages if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "memory_file_create"
    ]
    if not receipts:
        yield {0: DeltaToolCall(name="memory_file_create", json_args=json.dumps({
            "memory": "user",
            "path": "preferences.md",
            "content": "Language preferences\nReply in Chinese.\n",
        }))}
    else:
        assert receipts[-1].content["path"] == "preferences.md"
        yield "Preference saved."


async def read_preference(messages, info):
    prompt = "\n".join(
        str(part.content) for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, UserPromptPart)
    )
    assert "<memory-context" in prompt and "Reply in Chinese." in prompt
    assert "memory_file_create" not in {tool.name for tool in info.function_tools}
    yield "Your preferred language is Chinese."


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = DirectoryFileStore(directory)
        writer_cursors = MemoryCursors()
        writer = HarnessBuilder().build(
            AgentSpec(), output_type=str,
            model=FunctionModel(stream_function=write_preference),
            capabilities=(FileMemoryCapability(
                [FileMount("user", store, "write")], cursors=writer_cursors,
            ),),
        )
        first = await writer.run("Remember my preferred language.")
        print(first.output_or_raise())
        assert (Path(directory) / "preferences.md").exists()

        # Share the store; give the new Thread its own cursor and access policy.
        reader = HarnessBuilder().build(
            AgentSpec(), output_type=str,
            model=FunctionModel(stream_function=read_preference),
            capabilities=(FileMemoryCapability(
                [FileMount("user", store, "read", always_load=("preferences.md",))],
                cursors=MemoryCursors(),
            ),),
        )
        second = await reader.run("What language should you use?")
        assert second.thread_id != first.thread_id
        print(second.output_or_raise())


asyncio.run(main())
```

运行 `python memory_demo.py`，输出为 `Preference saved.` 和 `Your preferred language is Chinese.`。断言确认第二次 Run 属于另一个 Thread，因此偏好来自共享存储，而非前一次对话的历史。

示例结束时会删除临时目录。实际应用应使用 `DirectoryFileStore(".memory/user")` 这样的持久目录，以及已配置的 [Model](models.md)，由 Model 根据用户请求选择记忆工具。目录的保留和备份由 Host 负责。

`user` 是记忆挂载名，与 Environment 挂载不同。如果要求所有写入都经过记忆权限和版本检查，应将后端目录放在 Agent 可访问的文件系统之外。

### 模型获得的内容

每个挂载提供三类内容：

1. \*\*指引：\*\*挂载名称、访问模式，以及哪些内容适合保存。`guide=None` 使用 `DEFAULT_FILE_GUIDE`；也可提供自己的指引，或用 `""` 省略。
2. \*\*工具：\*\*下表中的 `memory_file_*` 操作。`read` 挂载提供 view 和 grep。`tools=("view", "grep")` 限制该 Capability 所有挂载的工具；权限规则使用 `memory.file.view`、`memory.file.delete` 等 ID。
3. **起始上下文：**`<memory-context>` 块提供索引和指定文件的正文。记忆正文是对话写入的数据，挂载指引才是使用这些数据的指令。

| 工具                 | 用途                                   | 必须满足的条件                   |
| -------------------- | -------------------------------------- | -------------------------------- |
| `memory_file_view`   | 列出目录，或读取文件及其版本           | 路径存在                         |
| `memory_file_grep`   | 查找匹配行，默认按字面匹配且忽略大小写 | 使用 `regex=True` 时，表达式有效 |
| `memory_file_create` | 创建文件                               | 路径尚未占用                     |
| `memory_file_edit`   | 将 `old_string` 替换为 `new_string`    | 原文字恰好出现一次               |
| `memory_file_append` | 在文件末尾追加文字                     | 文件存在                         |
| `memory_file_move`   | 重命名文件                             | 源文件存在，目标路径未占用       |
| `memory_file_delete` | 删除模型查看过的版本                   | 该版本仍是当前版本               |

写入还会检查该对话在同一份记忆中观察过的文件。假设 Thread A 读过偏好文件，随后 Thread B 修改了其中的语言偏好，那么 A 的下一次编辑会返回 `read_conflict`，即使 A 要替换的那段文字仍然匹配。继续修改时：

1. 重新读取发生变化的文件。
2. 根据新内容重新判断要做的修改。
3. 在后续模型步骤中发出新的写入。

同一批工具调用中的重读不能刷新该响应已经决定执行的写入。失败详情也不算重新读取。失败调用不会修改存储，之前成功的调用则保持已提交状态。

这些观察记录随 `HarnessState` 保存。读取过的文件、grep 命中、单独列出的索引条目和自动加载的文件都会形成版本检查；折叠目录的计数不会。未观察的无关文件可以自由变化。检查范围是一份记忆，不跨多个存储。如果进程在写入完成后、保存结果前停止，应先查看文件，再决定是否重新写入。

### 编写合适的记忆文件

每个主题使用独立的相对路径，并写清楚描述。文件采用 UTF-8，默认最大 64 KiB；目录隐式存在。索引优先采用 frontmatter 的 `description`，否则使用第一个非空行：

```markdown
---
description: Language and tone preferences
---
- Reply in Chinese.
- Keep answers short.
```

对起始时就需要的小文件，使用 `always_load=("preferences.md",)`。这个列表由 Host 指定，正文在上下文预算内优先加载；其他文件仍可通过索引和工具查找。

### 在多次 Run 之间控制上下文大小

每个 Thread 保留自己的 `MemoryCursors`，记录该 Thread 已收到哪些记忆上下文。后续 Run 接收变化的路径和有变化的自动加载文件；没有变化时不添加块。不提供游标时，每次 Run 都接收完整上下文。`DirectoryFileStore` 没有变更日志，因此发生任何变化后都会刷新完整上下文，而非返回增量列表。

将 `cursors.snapshot()` 与接受的 `HarnessState` 一起保存；游标不会自动嵌入该状态。续接时，重新挂载同一存储并恢复两者。下面的函数接收已保存的值和配置好的 Model，返回 Host 应当一起保存的新值：

```python
from pydantic_ai.models import Model

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def continue_with_memory(
    directory: str, state_json: str, saved_cursors: dict[str, str | None], model: Model,
) -> tuple[str, str, dict[str, str | None]]:
    cursors = MemoryCursors(saved_cursors)
    memory = FileMemoryCapability(
        [FileMount("user", DirectoryFileStore(directory), "write")], cursors=cursors,
    )
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model, capabilities=(memory,),
    )
    result = await executable.run(
        "Continue using my saved preferences.",
        previous_state=HarnessState.model_validate_json(state_json),
    )
    output = result.output_or_raise()
    assert result.state is not None
    return output, result.state.model_dump_json(), cursors.snapshot()
```

确认首次结果成功后，用 `first.state.model_dump_json()` 和 `writer_cursors.snapshot()` 取得初始值。续接应使用持久目录；前面的演示会主动删除临时存储。Host 重建挂载和访问策略，文件版本观察记录则随 `HarnessState` 恢复。主历史经压缩或 handoff 替换时，Capability 会清空观察记录和上下文游标，让下次普通 Run 获得新的记忆上下文。

要调整预算，在打开 `store` 并创建该 Thread 的 `cursors` 的作用域内使用以下配置，再将 `memory` 传给 builder：

```python
from a13n_harness.capabilities import FileMemoryLimits

limits = FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096, write_retries=3)
memory = FileMemoryCapability([FileMount("user", store, "write")], limits=limits, cursors=cursors)
```

一次 Run 的所有文件记忆共用 `context_bytes`，包装文本和读取提示也计入预算。自动加载的正文有大小限制，索引使用剩余空间。大索引先把目录折叠成 `archive/ (37 files)` 这样的条目，再按需截断。如果连最小块都放不下，会统一替换为一条简短的读取与清理提示。省略的正文通过 `memory_file_view` 读取：截断块送达后仍会推进游标，没有变化的省略文件不会自动补发。

### 在多个进程之间共享记忆

让各进程的 `DirectoryFileStore` 指向同一目录，每个 Thread 仍分别保存游标和 `HarnessState`。存储修改使用隐藏目录 `.a13n-memory/` 中的跨进程锁。直接编辑文件会绕过这个锁，因此应用中的并发写入也应经过存储接口。

目录存储保存当前文件，不保留修订历史。请为目录做备份；如果产品需要恢复历史版本，可采用支持历史的 `FileStore`，例如 Service 自己实现的文件记忆存储。

### 使用自己的文件存储

如果记忆应保存在其他后端，实现 `a13n_harness.providers.memory` 中的 `FileStore`：

1. 为一份记忆命名空间提供 `list`、`read`、`write`、`move`、`delete`、`changes` 和 `purge`。
2. 为每个文件提供不透明的版本值。在同一事务或锁内检查目标预期版本、修改的 `dependencies` 并完成修改。依赖项将路径映射到观察过的版本，`None` 表示路径原先不存在。不匹配时，必须在产生任何存储变更前抛出 `read_conflict`。
3. 通过 `changes` 返回变更游标；无法列出增量时返回 `FullResync`。使用 `validate_path()` 和 `describe()` 应用公共路径与文件规则。
4. 按需实现 `SearchableFileStore.search`。每条命中的版本与文字必须来自同一次读取；否则 Harness 会读取文件来实现 grep。

保留历史的存储可以使用 `Origin`，将修改归属到 Run、principal 和工具调用。[文件记忆规范](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21-file-memory.md)列出了完整签名、预算和失败码。

## 记录记忆

对于“偏好绿茶”这类应按含义检索的事实，选择记录记忆。后端根据 Run 的输入对记录排序。每条记录都有 ID，更新会替换正文，不使用文件记忆的版本检查。

### 挂载 mem0

运行示例前，在 `http://localhost:8888` 启动 [mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api)，并按[快速开始](getting-started.md)配置 Model 凭据。使用测试命名空间：程序会要求 Model 保存一条持久记录。如果服务器要求认证，将 API key 以 `{"api_key": ...}` 凭据映射传给 `MEM0_OSS.open`。

`MEM0_OSS.open` 将存储绑定到 `namespace="alice"`，`RecordMount` 再以 `facts` 向 Model 提供它。显式传入的 HTTP 客户端允许访问本地地址。两个上下文管理器都保持打开，直到 Run 完成：

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

第二次 Run 启动新的 Thread，依据自己的输入从同一存储召回记录，用保存的偏好回答咖啡馆点单问题。使用真实 Model 时，应检查记忆工具结果来确认写入，回答措辞和工具选择可能不同。

命名空间对应 mem0 的 `user_id`，应按共享记忆的用户或群组明确分配。两种 provider 都原样添加文字、检查命名空间归属，并回读确认写入。

使用托管 mem0 时，改用 `MEM0_PLATFORM` 及其 API key。下面的函数接收已配置的 Model 和 Host 获取的 key，从应用的异步入口调用它：

```python
from pydantic_ai.models import Model

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMount
from a13n_harness.providers.memory import MEM0_PLATFORM


async def remember_in_platform(model: Model, api_key: str) -> str:
    async with MEM0_PLATFORM.open({}, {"api_key": api_key}, namespace="alice") as store:
        memory = RecordMemoryCapability([RecordMount("facts", store, "write")])
        executable = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=model, capabilities=(memory,),
        )
        result = await executable.run("Remember that I drink green tea.")
        return result.output_or_raise()
```

Platform 默认地址为 `https://api.mem0.ai`。未传入 `http` 时，provider 自行创建并关闭客户端，该客户端只能访问公开 HTTPS 地址。显式传入的客户端仍由调用方负责关闭，如前面的本地示例所示。

### 模型从记录中获得的内容

每个挂载提供名称、访问模式和指引。`guide=None` 使用 `DEFAULT_RECORD_GUIDE`，也可提供自己的文字或用 `""` 省略。在 Run 的首次输入中，`<memory-recall>` 块提供最接近的记录。挂载设置 `recall=False` 可关闭召回；召回失败或超时会跳过，让 Run 继续执行。

| 工具                   | 用途             | 必须满足的条件                      |
| ---------------------- | ---------------- | ----------------------------------- |
| `memory_record_search` | 按含义查找记录   | 存储能够回答查询                    |
| `memory_record_list`   | 读取一页记录     | 使用返回的 `next_cursor` 继续读取   |
| `memory_record_add`    | 保存一条简短事实 | 文字非空白，且不超过 `record_chars` |
| `memory_record_update` | 替换记录正文     | 记录属于这份记忆                    |
| `memory_record_delete` | 删除记录         | 记录属于这份记忆                    |

`read` 挂载提供搜索和列表。`tools=("search", "add")` 限制该 Capability 各挂载的工具；工具权限 ID 从 `memory.record.search` 到 `memory.record.delete`。

写入返回 `write_unconfirmed` 时，先搜索存储，再决定是否重写。即使确认失败，后端也可能已经接受了第一次请求，自动重试可能产生重复事实。

在记录 `store` 打开期间配置大小和召回限制，再将这个 `memory` 传给 builder：

```python
from a13n_harness.capabilities import RecordMemoryLimits

limits = RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192, recall_seconds=2.0)
memory = RecordMemoryCapability([RecordMount("facts", store, "write")], limits=limits)
```

每份记忆最多召回 `recall_limit` 条记录，在 `recall_bytes` 内优先保留最接近的结果。`recall_seconds` 限制召回等待时间，`record_chars` 限制工具写入的文字长度。

### 使用自己的记录存储

实现 `a13n_harness.providers.memory` 中的 `RecordStore`，提供 `search`、基于游标的 `list`、`add`、`update`、`delete` 和 `purge`，全部绑定到一个命名空间。使用 `validate_record_text()` 校验文字，通过 `MemoryStoreError` 报告失败。将结果不确定的写入与已确认的拒绝区分开，让调用方知道何时应先检查现有记录。

完整存储协议、mem0 行为和召回规则见[记录记忆规范](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21a-record-memory.md)。
