---
title: 记忆
description: 在跨对话中沉淀团队长期知识，运行自动化 Dreams 风格提炼工作流，并在发布前审查候选差异（Diff）。
---

在 Service 中使用**记忆空间（Memory Spaces）**（带自动整理与候选审查的团队/会话 Markdown 知识库）或[独立记忆](standalone-memory.md)（挂载在线程上的独立文件树与 Mem0 语义记录）跨会话沉淀长期知识。

| 业务需求                                 | 推荐方案                                                  |
| ---------------------------------------- | --------------------------------------------------------- |
| 团队共享知识、会话交接笔记与候选差异审查 | [记忆空间（Memory Spaces）](#memory-spaces)               |
| 绑定到特定线程的独立版本化文件树         | [独立文件记忆](standalone-memory.md#create-a-file-memory) |
| 按语义相似度检索的短事实记录             | [Mem0 记录记忆](standalone-memory.md#record-memories)     |

## 记忆空间（Memory Spaces）

每个记忆空间（通过 `POST /api/v1/memory-spaces` 传入 `{"name": "Engineering Guidelines"}`，或在 **Memories → Spaces → Create Space** 创建）按 `global/`（其中 `global/MEMORY.md` 直接加载进模型提示词）与 `sessions/{session_id}/` 组织文件。

在智能体版本或会话上挂载记忆空间：

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "team_kb", "space_id": "mspace_abc123", "access": "write"}]
  }
}
```

多智能体并发写入受乐观锁版本保护（发生 `read_conflict` 时智能体会先通过 `memory_file_view` 重新读取最新内容再重试）。可在 Console 消息的 **Debug info** 或流式 `TEXT_MESSAGE_END` 事件的 `memory_sources` 字段中审计实际注入的记忆来源。

### 自动整理与历史对话提炼

在空间上启用 **Organizer** 自动整理策略（或在 Console 点击 **Organize now**），后台会自动去重归并笔记并更新 `global/MEMORY.md`：

```json
{
  "organization": {
    "enabled": true,
    "model": "model-gemini-pro",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {"type": "change_count", "count": 10}
  }
}
```

### 自动从对话中提取知识

通过 `POST /api/v1/memory-organizations` 提交按需整理或历史对话挖掘任务（最多指定 100 个 `session_ids`）。默认设置 `"output": "candidate"` 会将改动暂存在隔离的\*\*候选树（Candidate Tree）\*\*中，供管理员在 Console（**Review candidate → Apply all changes**）逐文件对比 Diff 后原子合并上线：

```json
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "output": "candidate",
  "instructions": "Extract verified architecture decisions and production deployment policies.",
  "history": {
    "session_ids": ["ses_backend_refactor", "ses_auth_redesign"],
    "after": "2026-10-01T00:00:00Z",
    "max_runs": 50
  }
}
```

## 独立记忆

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span> <span id="what-the-model-gets"></span>

- [独立文件记忆与记录记忆参考](standalone-memory.md)
