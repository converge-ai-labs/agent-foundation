---
title: Skill 发布参考
description: 选择共享或隔离软件包发布方式，并处理清理与不确定结果。
---

先[导入并测试工作空间 skill](skills.md#add-a-skill)，再选择发布方式。常规路径使用共享发布，并发 agent 需要不同软件包版本时使用隔离发布。这些规则适用于导入的工作空间 skill，不适用于宿主只读内置 skill。

## 运行时同步

在 agent 的高级 JSON 中选择发布模式。模式决定 skill 文件在主环境中的位置：

| 需求                          | 模式             |
| ----------------------------- | ---------------- |
| 使用约定的共享 skill 路径     | `shared`（默认） |
| 同时运行同名 skill 的不同版本 | `isolated`       |

选择导入的工作空间 skill 或要求共享 cleanup 都需要主环境。每个子 agent 使用自己的策略。

### 共享目录

以下配置片段选择一个 skill，并显式写出默认值。将 `sk_...` 替换为导入的 skill ID：

```json
{
  "skills": [{"skill_id": "sk_..."}],
  "skill_sync": {"mode": "shared", "overwrite": true, "cleanup": false}
}
```

首次模型请求前，Service 将每个选中的包发布到 `/workspace/.agents/skills/<name>`。`overwrite: true` 替换整个目录并移除旧文件。设置 `overwrite: false` 会保留已有目录，即使其内容与选中版本不同。

独立 agent 共享环境时，保留 `cleanup: false`。启用 cleanup 会从共享根目录删除未选中的条目；空选择会清空它，包括其他 agent 正在使用的 skill。共享模式不使用包缓存。

同名 skill 以最后一次发布为准。因此活跃读取者可能看到另一个运行发布的版本，多次读取也可能跨越版本。发布以单个 skill 为单位，不会原子更新整组选项。各 agent 需要持续读取自己选中的包时，使用隔离模式。

### 隔离包

设置以下片段，让不同包版本并存：

```json
{"skill_sync": {"mode": "isolated", "cache_count": 128}}
```

目录指向 `/workspace/.agents/skill-cache/<package-digest>/<name>`。相同归档复用一个目录，不同版本使用不同路径。`cache_count` 是包数量软预算。各 agent 按自己的预算移除空闲包，同时保留活跃包，因此缓存可能超出上限。设为 `0` 可禁用清理。

共享 cleanup 不会触碰此缓存。Service 不覆盖已经发布的隔离包，但授权的文件和 shell 操作仍能修改它们。隔离发布用于分离版本，不会把文件设为只读。

### 覆盖单次运行

要让单条消息使用隔离模式，在提交中加入此片段：

```json
{"options": {"overrides": {"skill_sync": {"mode": "isolated"}}}}
```

覆盖替换整个配置块，省略字段使用所选模式的默认值。`overwrite` 和 `cleanup` 属于共享模式，`cache_count` 属于隔离模式。子 agent 保留自身策略。

### 发布与清理

Service 先暂存完整包，再发布其目录。在支持的 Linux 和 macOS 文件系统上，原子替换会在发布失败时保留旧目录。Windows 对已有目录使用删除后重命名；读取者可能看到空档，失败可能留下缺失或部分删除的目标。其他不支持的原子替换会失败，并保留旧目录。

取消或 provider 结果不确定后，先检查环境，再决定重试或清理：替换可能已发布，也可能留有暂存内容和缓存保护。手动回收遗留暂存目录、pin 或淘汰 gate 前，先停止所有客户端。这些保护不会按时间自动过期；缓存协调依赖客户端之间一致的共享文件系统操作。旧 `.a13n/skills` 布局既不读取，也不迁移。
