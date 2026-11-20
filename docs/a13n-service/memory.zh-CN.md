---
title: 记忆
description: 跨会话长期沉淀团队知识库，运行受 Dreams 启发的自动化知识提炼工作流，并在发布前审查变更 Diff。
---

在多智能体业务系统中，关键的用户偏好、架构决策与工程规程应当跨越单次会话长期存在。**Service 记忆空间（Memory Spaces）**为团队提供了具备版本追踪能力的结构化文件存储系统，供各个智能体在不同会话中按需检索与持续沉淀。

Service 将该持久化空间与自动化的**知识整理引擎（Organizer Engine）**深度结合（灵感源自人类睡眠时大脑记忆巩固的 "Dreams" 机制）：系统可在后台自动提炼过去多场对话中的事实要点，化解冲突信息，并在一个完全隔离的候选空间中生成全量 Diff，待管理员审查批准后原子合并入生产知识库。

| 团队场景需求 | 推荐记忆模式 |
| :--- | :--- |
| **团队共享知识库与会话独立交接笔记** | [记忆空间 Memory Space](#memory-spaces) |
| **仅为特定单个线程挂载的版本化独立文件树** | [独立文件记忆](standalone-memory.md#create-a-file-memory) |
| **基于 Mem0 的简短事实语义向量检索** | [记录记忆 Record Memory](standalone-memory.md#record-memories) |

## 记忆空间

一个 **Memory Space** 内部拥有清晰的规范化文件目录结构：

- `global/`：存放全公司/全团队通用的技术规范、工程准则以及编码偏好，面向所有会话开放。
- `global/MEMORY.md`：核心全局记忆摘要索引，会在会话建立时按需注入 Agent 的系统提示词中。
- `sessions/{session_id}/`：各独立会话的私有专属目录，供智能体记录该次会话特定的操作备忘与任务交接记录。

### 选择空间

1. 在 Console 侧边栏进入 **Memories → Spaces**，点击 **Create Space**（例如命名为 `Engineering Guidelines`）。
2. 打开目标智能体的配置详情，开启 Memory 支持，并将该空间以自定义挂载名（如 `team_kb`）挂载为 `write` 可写权限。
3. 开启一个测试会话并指示 Agent：
   > *“请记录一条我们团队的数据库建表规范：所有新建业务表必须在 global/conventions.md 中显式包含 created_at 和 updated_at 的 UTC 时间戳字段。”*
4. 打开该记忆空间的 **Files** 文件列表标签页，您会看到该 Markdown 文件已被精确提交，并标注了作者身份与版本时间戳。
5. 开启一个全新的独立会话，选用挂载了相同空间的另一款 Agent 提问：*“我们团队关于数据库时间戳的规范是什么？”*，Agent 会自动检索该空间并给出精准回答。

通过 HTTP API，调用 `POST /api/v1/memory-spaces` 创建空间：

```json
{
  "name": "Engineering Guidelines"
}
```

在保存 Agent 版本或创建会话时传入 Space ID 即可挂载：

```json
{
  "memory": {
    "enabled": true,
    "spaces": [
      {
        "name": "team_kb",
        "space_id": "mspace_abc123",
        "access": "write"
      }
    ]
  }
}
```

### 整理已保存的知识

随着业务的推进，分散在各个文件和会话目录中的笔记难免产生信息冗余与碎片化。**知识整理引擎（Organizer Engine）**能够在后台定期巡检、提炼归并重复项，并同步刷新核心的 `global/MEMORY.md` 索引。

若需手动触发单次知识整理：

1. 在 Console 侧边栏进入 **Memories → Spaces → Organization**。
2. 选择一款具备强逻辑推理能力的大模型，点击 **Organize now**。
3. 系统会在后台拉起一个专用的隔离任务，全盘扫描已有文件，消除相互重叠的陈述，并生成结构化的知识网络。

如需全自动免运维治理，可在空间配置中开启**自动化整理策略（Automatic organization policy）**：

```json
{
  "organization": {
    "enabled": true,
    "model": "model-gemini-pro",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {
      "type": "change_count",
      "count": 10
    }
  }
}
```

在上述变更计数模式下，每当团队成员或智能体累计保存了 10 次文件变更，整理引擎便会自动调度一轮整理，且各轮之间维持 15 分钟的静默冷却期（Cooldown），兼顾知识新鲜度与 Token 成本。

### 自动提取对话知识

除了审计显式保存的文件，整理引擎还能直接从已封存（Sealed）的历史真实对话中主动挖掘隐式沉淀的技术决策：

```json
{
  "instructions": "提炼所有经过团队确认的架构设计方案、API 接口规范以及生产发布规程。",
  "history": {
    "session_ids": ["sess_backend_refactor", "sess_auth_redesign"],
    "after": "2026-11-01T00:00:00Z",
    "max_runs": 100
  }
}
```

整理引擎会自动分析指定时间窗口内的完整会话流，提炼具有明确共识的结论，并为知识库拟定追加补丁。

### 运行自定义整理任务

在面临重大项目发版或团队技术交接时，您可以针对特定主题按需下发单次深度知识提炼任务：

```json
POST /api/v1/memory-organizations
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "instructions": "系统梳理第四季度所有数据库分库分表的技术决议，并明确标出目前尚未完成数据平滑迁移的遗留表。"
}
```

系统会为该任务分配独立受控的后台 Run，并在审计界面中完整追踪其 Token 消耗与执行调用链。

### 从选定对话中提炼知识

精准提炼支持同时圈定最多 100 个历史会话作为提炼样本：

```json
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "instructions": "提取业务已确认的新功能排期，并忠实记录各方存在分歧的架构备选方案。",
  "history": {
    "session_ids": ["ses_feature_planning", "ses_sprint_review"],
    "after": "2026-10-01T00:00:00Z",
    "before": "2026-11-01T00:00:00Z",
    "max_runs": 50
  }
}
```

模型会将人类的发言作为最高权威证据，清晰区分用户的明确纠正与 Agent 的未证实猜测，并完整保留悬而未决的技术争鸣。

### 审阅候选后再发布

为防止大模型幻觉对企业生产知识库造成污染，自定义整理任务默认在完全隔离的**候选树（Candidate Tree）**中暂存变更：

```json
{
  "output": "candidate"
}
```

指定为 `"candidate"` 后，整理引擎的所有增删改操作均在一个临时克隆的影子空间中执行，生产环境的活跃知识文件完全不受影响。

1. 生成完成后，在 Console 中点击 **Review candidate** 进入全屏审查工作台。
2. 界面会展示本次整理新增、修改与删除的文件计数，并提供可展开的完整目录树。
3. 点击任意文件即可打开左右对比的 **Diff 差异视图**，直观比对生产基线与候选版本的每一处文本变更。
4. 逐个核对并将满意的条目标记为已审查。
5. 点击 **Apply all changes**，将候选树整体原子合并进入生产 Memory Space。

如果在整理期间生产空间发生了并发编辑，Service 会给出基线冲突警告（`baseline_changed`），允许您再次审视最新变更，并在确认无误后选择覆盖或重新生成。

### 组合 Dreams 风格工作流

一套完整的 Dreams 风格知识流转体系在企业内部形成闭环：

1. **日常交互协作：** 工程师与智能体在日常会话中推进业务，智能体在各自 session 目录中随手记录临时过程数据。
2. **夜间定时提炼：** 自动化整理策略在业务低峰期静默触发，扫描当天新产生的会话记录。
3. **沉淀与索引更新：** 引擎将高频模式提炼至 `global/` 空间，归档过时的临时笔记，并自动重写精简的 `global/MEMORY.md` 索引。
4. **人工卡点把关：** 团队管理员在控制台直观审查 Diff 差异并确认合并。
5. **全局智能升级：** 次日全员的所有新会话即可自动享受到最新沉淀的企业知识加持。

### 基于当前知识写入

为了防范多个智能体并发写入同一份知识文件时发生数据覆盖，Service 引入了严谨的乐观并发控制：

- 当智能体阅读某份记忆文件时，Harness 会记录其观察到的文件版本版本号。
- 当智能体下发写入指令时，Service 会原子校验该文件版本是否被其他会话修改过。
- 若版本已被更新，写入会被拦截并返回 `read_conflict`。
- 智能体底层会自动调用 `memory_file_view` 重新拉取最新版本，结合最新内容重新推导后再发起写入。

## 独立记忆

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span>

如果您的业务微服务需要为特定独立的对话线程绑定专属隔离的临时文件树，或挂载基于向量的语义记忆存储，请参阅[独立记忆参考手册](standalone-memory.md)。

## 查看回复加载的记忆

若需精确审计某条模型回复究竟引用了哪些长期记忆内容：

- 在 Console 控制台中，展开该条消息下方的 **Debug info（调试信息）** 面板。
- 在 API 接口流中，检查随 `TEXT_MESSAGE_END` 事件下发的 `memory_sources` 元数据字段。

每一条记录均准确展示了被注入 Prompt 的 Space ID、挂载路径、SHA-256 内容摘要与字数规模，满足企业级合规审计要求。
