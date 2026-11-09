---
title: 记忆
description: 保存知识，在另一段对话中复用，并检查 agent 做过的修改。
---

用记忆保存需要跨对话保留的偏好、决策和参考事实。共享知识和会话笔记可以从记忆空间开始；应用需要在线程上直接连接指定存储时，选择独立记忆。

| 需求                                   | 选择                                                        |
| -------------------------------------- | ----------------------------------------------------------- |
| 共享项目知识，同时保留各会话自己的笔记 | [记忆空间](#memory-spaces)                                  |
| 在指定线程上挂载版本化文件树           | [独立文件型记忆](standalone-memory.md#create-a-file-memory) |
| 从 mem0 按语义检索简短事实             | [记录型记忆](standalone-memory.md#record-memories)          |

记忆属于工作空间，无需计算环境。示例中的 `A13N_URL` 是 Service 基础地址，`A13N_API_KEY` 是限定到工作空间的密钥。下文 API 路径均位于 `/api/v1` 下。

## 记忆空间

**记忆空间**在一个版本化文件存储中保存共享知识和会话笔记。先完成一次保存与复用，再配置自动整理：

1. 打开**记忆 → 空间**，创建名为 `Product knowledge` 的空间。
2. 在 agent 的**记忆**区域启用参与。选择该空间，使用挂载名 `product` 并授予写权限。保持 agent 的记忆工具启用。
3. 用该 agent 开始新会话，请它：“将我们的文档语言偏好 English 保存到 `product` 记忆中的 `global/preferences.md`。”
4. 打开空间的**文件**，核对保存内容。在**文件历史**中查看作者和修订记录。
5. 新建另一个选择了相同空间的会话，请 agent 读取 `global/preferences.md` 并报告保存的偏好。

普通运行可访问 `global/` 和自己的 `sessions/{session_id}/` 文件夹。共享知识放入 `global/`，对话专属笔记放入会话文件夹。上下文默认包含 `global/MEMORY.md`，以及会话的 `MEMORY.md`（如果存在）。通过 Console 和 API 管理空间中的所有文件时，使用工作空间权限。

选择**通用**以维护可复用的主题知识，或选择**通用＋每日记录**，同时按日期保留重要事件。在**自定义指南（Custom guide）**中填写业务关注点。**高级设置**包含时区和可编辑的**系统规则（System guide）**。普通 Agent 和整理器使用同一套规则。修改只影响新 Run，不会自动重组已有文件。创建和更新空间时，可使用与[独立文件型记忆](standalone-memory.md#create-a-file-memory)相同的 `guidance` 对象。

### 选择空间

使用 `POST /memory-spaces` 创建空间，需要 `write`：

```json
{"name": "Product knowledge"}
```

将返回的 `id` 用于 agent 的 `config.memory`，或用于 `POST /sessions`、`PATCH /sessions/{session_id}`：

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "product", "space_id": "mspace_...", "access": "write"}]
  }
}
```

会话选择需要 `run`；PATCH 还需要会话的 `If-Match`。名称和空间 ID 各只能出现一次。省略的字段在首次接收业务运行时继承 agent 默认值。显式 `spaces` 列表替换整个选择；`[]` 不提供空间上下文或工具。后续修改 agent 或创建分支时，会话保留已经确定的选择。通过会话顶部的**记忆**控制修改后续运行。

关闭参与会保留选择。停用空间会保留文件、历史和绑定，同时将其从新运行中排除。已接收的运行保留选定挂载。空间挂载与独立线程挂载共用数量限制，且必须使用不同名称。

### 整理已保存的知识

空间已有有用文件后，在**配置 → 整理**中设置整理模型，选择**立即整理**。整理器读取共享知识和所有会话文件夹，维护 `global/MEMORY.md`。在**整理**标签页检查生成的文件和修订记录。每轮在专用会话中使用新线程，只提供该空间的记忆工具，最多调用模型 200 次。

启用**自动整理**以处理后续保存的变更。选择**有变更即触发**或**达到修改次数后触发**。例如，下面的创建或更新片段每分钟检查一次，要求累计十次修改，两轮之间至少等待十五分钟：

```json
{
  "organization": {
    "enabled": true,
    "model": "your-model-key",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {"type": "change_count", "count": 10}
  }
}
```

次数模式包括 agent、Console 和 API 已保存的文件修改；重命名计一次。内容未变的保存、被拒绝的写入、历史清理和整理器自身的修改不计数。已接收的轮次消耗开始时已有的修改次数，失败或取消也一样。轮次期间保存的修改留给后续处理。计数不受重启和历史清理影响；已有存储在安装此能力后开始累计新修改。

**检查间隔**默认 10 秒（范围 10–86400）；**冷却时间**默认 300 秒（范围 0–604800）。自动整理必须同时满足触发条件和冷却要求，由共用的每 10 秒服务扫描进行检查。这些值表示最短等待时间。已记录的期限保持原值，策略修改用于后续检查和轮次。**立即整理**绕过检查间隔、次数门槛和冷却。

**整理**标签页展示状态、费用、追踪和修订记录，与业务对话分开。失败或取消的轮次保留已保存的修改。历史缺口状态表示保留的修订不足以确定进度；可用**立即整理**请求新一轮。

API 客户端通过 `GET /memory-spaces/{id}/organization` 读取状态，通过 `POST /memory-spaces/{id}/organizations` 请求整理，用同一路径的 `GET` 列出轮次。`/memory-spaces/{id}/sessions` 列出关联业务会话。列表使用 `limit` 和 `cursor`。文件和修订 API 使用空间的 `memory_id`，位于 `/memories/{memory_id}` 下。通过空间管理底层存储；它不出现在独立记忆列表中，也不能直接挂载。

### 自动提取对话知识

自动策略可包含任务 `instructions` 和 `history` 选择。指定对话会话 ID 和显式起始封存时间（`after`）；可选 `before` 关闭时间窗口，`max_runs` 限制每批数量（默认 100，最大 1000）。例如，在空间的 `organization` 策略中添加：

```json
{
  "instructions": "Maintain product decisions and current handoff notes.",
  "history": {
    "session_ids": ["sess_..."],
    "after": "2026-11-01T00:00:00Z",
    "max_runs": 50
  }
}
```

未处理的已封存 Run 即使没有文件修改、记忆为空也能触发整理。完成的轮次消耗选定 Run；失败和取消的轮次在冷却后仍可重试。每批保留精确来源身份。文件修改计数与历史进度独立，因此清理修订和并发文件修改不会重放已成功处理的对话。修改选择保留已完成的历史进度。一次性整理任务不推进自动进度。

独立文件型记忆使用同一策略，无需创建空间。先调用 `GET /memories/{id}/organization`，再用返回的 ETag 作为 `If-Match`，通过 `PUT` 替换策略。响应包含策略配置、状态、`processed_history_runs` 和配置版本；状态变化不改变策略 ETag。新独立策略默认关闭。托管底层记忆使用所属空间的策略。每轮接收前，服务重新检查配置者的当前权限和已配置资源。

### 运行自定义整理任务

通过 `POST /memory-organizations` 使用自己选择的模型和指令，整理独立文件型记忆或已启用的空间。需要工作区 `read`、`write` 和 `run` 权限。无需配置自动策略，空存储也可以使用：

```json
{
  "target": {"kind": "memory", "id": "mem_..."},
  "model": "your-model-key",
  "instructions": "Consolidate supported product decisions and preserve unresolved disagreements."
}
```

整理空间时，使用 `{"kind": "space", "id": "mspace_..."}`。不能直接选择空间托管的底层记忆。响应标识整理请求及其普通运行。通过 `GET /memory-organizations/{id}` 读取请求，使用运行 API 查看状态、取消、用量和追踪。每次提交接受独立任务，包括响应结果不明后的重试；不同任务可以并发整理同一目标，文件写入仍执行常规冲突检查。每个任务保留独立指令与调用者权限。失败或取消的运行保留已提交的修改。

API 字段 `guidance.modules` 可以独立组合 `topics`、`journal` 和 `session_notes`。例如，`{"modules": ["topics", "session_notes"], "timezone": "UTC"}` 同时保留可复用知识和当前交接状态。省略 `modules` 时使用选定预设；`[]` 仅保留共用维护规则。系统覆盖文本替换完整的组合指南。通过 `GET /memories/guidance-presets` 读取可用模块文本。

### 从选定对话中提炼知识

在自定义整理请求中加入 `history`，即可从一个或多个对话 Session 提炼知识，也可写入空记忆：

```json
{
  "target": {"kind": "memory", "id": "mem_..."},
  "model": "your-model-key",
  "instructions": "Extract supported product decisions, cite source Runs and retain conflicting statements.",
  "history": {
    "session_ids": ["ses_first", "ses_second"],
    "after": "2026-01-01T00:00:00Z",
    "before": "2026-02-01T00:00:00Z",
    "max_runs": 100
  }
}
```

选择同一工作区内 1–100 个不重复的对话 Session。可选时间边界包含端点，按运行封存时间筛选，而非消息时间。受理时，Service 冻结匹配的已封存运行，并在 `sources` 中返回来源 Session、Thread 和 Run 标识。此后新增的对话不会改变选择。`max_runs` 默认为 100，最高 1000；超出上限会拒绝请求，需要缩小时间范围或拆分 Session。空选择不提供历史事实。

整理器通过有界来源查询与内容读取、搜索工具读取选定范围。它将对话视为证据，保留更正与分歧，并区分用户陈述和 agent 推断。省略 `history` 时仅整理已保存文件。升级不会自动扫描旧对话或消耗模型 token。

### 审阅候选后再发布

在自定义整理请求中设置 `"output": "candidate"`，生成期间保持正式文件不变。响应中的 `candidate_memory_id` 标识从目标复制出的独立文件型记忆。使用普通记忆 API 检查或编辑候选文件，也可以同时选择对话历史来源。

1. 等待普通运行完成并封存。通过 `GET /memory-organizations/{id}/candidate` 查看状态、变更路径、`candidate_seq` 和 `target_seq`。
2. 检查 `GET /memory-organizations/{id}/candidate/diff?path=...`。它比较受理时基线与候选内容。预览标记 `truncated` 时，需要打开完整文件。目标已变化时，还应单独审阅当前目标。
3. 通过 `POST /memory-organizations/{id}/candidate/apply` 发布，例如 `{"mode": "checked", "candidate_seq": 7, "target_seq": 4}`，填写实际审阅的序列号。完整文件树原子发布。未变化的目标文件保留 ID 和版本。

`review_changed` 表示候选或目标在审阅后变化，需要重新加载两者再决定。`baseline_changed` 表示目标在生成开始后发生变化。可以重新生成候选，或审阅当前目标后显式选择 `replace_all`。`replace_all` 覆盖完整目标文件树并删除仅存在于目标中的文件，不会合并并发编辑。同一个完整应用请求可以安全重试，并返回已存储的回执。

通过 `POST /memory-organizations/{id}/candidate/discard` 放弃已结束的候选。活动运行需要先通过运行 API 取消。失败或取消的生成不能发布。应用需要当前写权限，目标空间也必须处于启用状态。放弃与应用均保留独立候选记忆供检查，不再需要时单独删除。删除目标或候选后无法发布。发布仅更改文件内容，不改变目标指南或 Session 绑定。

### 基于当前知识写入

Agent 与整理器会跟踪对话已读取的文件版本。每次文件修改都会原子校验同一份记忆中的这些观察记录，并执行写入。源文件或目标文件变化时返回 `read_conflict`，指出需要重读的文件。

继续操作时，agent 用 `memory_file_view` 重读文件，在下一条响应中考虑新内容，再次写入。Harness 自动提供版本条件。与重读一同发出的写入仍使用旧观察记录。观察记录随对话检查点保存。独立文件型记忆使用相同流程。

## 独立记忆

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span>

- [独立记忆参考](standalone-memory.md)

## 查看回复加载的记忆

要展示自动加载了哪些记忆，将 `memory_sources` 与回复一起保存。线程 SSE 流在 `TEXT_MESSAGE_END` 的 `delta.item.memory_sources` 中提供此数据；运行条目在 `content.memory_sources` 中提供。流中的 boundary 确认检查点持久化；仅消息结束不足以确认。

每项标识挂载（`memory`）和资源（`memory_id`）。记录项包含召回 ID 和文本 SHA-256 摘要。文件项包含交付状态，以及实际加载内容的路径、摘要、字节数和截断标记。仅列入索引不表示已读取文件内容。`sources_truncated` 表示引用列表不完整。

这可用于确认自动交付内容，不能证明模型使用了某条事实。它不包含继承的对话历史和显式工具读取。字段缺失表示未捕获来源；空列表需结合 `status` 解读。旧回复不会回填来源。

历史查询返回已保存的来源，不会重新查询 provider。后续编辑或删除记忆不会改变这些信息。摘要无法重建早期文本；查看当前内容仍需要有权限的记忆 API 请求，以及可用的资源。
