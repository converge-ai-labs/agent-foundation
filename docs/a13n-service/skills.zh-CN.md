---
title: Skill
description: 将指令和文件打包为带修订版本的 skill，供 agent 按需加载。
---

Skill 是一组指令和配套文件，agent 在任务需要时可以加载。Skill 使用[修订版本](resources.md#lifecycles)管理：每个新软件包新增一个不可变的修订版本，agent 修订版本固定引用精确的 skill 修订版本。运行时的使用方式请参阅 [Harness skill](../a13n-harness/skills.md)。

Skill 通过 [ID](resources.md#common-conventions)（`sk_…`）标识，用于 `/api/v1/skills/{skill_id}` 等路径和 agent 配置。模型看到的名称来自固定修订版本的 `SKILL.md` 中声明的 `name`。同一工作空间的两个 skill 可以声明相同名称，新修订版本也可以改名，但同一 agent 的 skill 必须声明互不相同的名称。

## 内置技能

**Skills** 页面也会显示随 Service 发布的内置技能。在 Agent 的 **Skills** 区域勾选 **platform-operations** 并保存版本；对应 API 字段为 `builtin_skills: ["platform-operations"]`。`GET /api/v1/builtin-skills` 返回名称和描述。这些条目没有工作空间资源 ID、版本或导入、编辑操作，勾选也不会授予平台工具或文件权限。

点击内置技能可打开只读文件详情，默认展示 `SKILL.md`。你可以查看参考文档和当前部署的能力清单，切换 Markdown 预览与源码，或下载 ZIP。内容反映当前 Service 部署，随部署更新，不提供独立的版本历史。

用户 Agent（包括 Agent Composer 的副本）需要主环境。内置技能和导入技能使用相同的 `skill_sync` 策略，声明的名称不能重复。共享模式下，说明位于 `/workspace/.agents/skills/platform-operations/SKILL.md`；隔离模式使用下文的包摘要目录。包中包含 `schemas/agent-config.json` 和 `instance/catalog.json`，文档使用相对引用。内置 Agent 继续使用 Host 上的只读文件，无需用户环境。已有的用户 Agent 如果启用了内置技能，需要选择会话环境或配置默认环境模板；缺少主环境时，运行会在调用模型前失败。

## 软件包格式

Skill 包是 zip 归档，`SKILL.md` 位于根目录或唯一的顶层目录中。`SKILL.md` 使用 UTF-8 编码，YAML front matter 至少声明 `name` 和 `description`：

```markdown
---
name: release-notes
description: Write release notes from merged pull requests.
---

# Release notes

1. List the merged pull requests since the last tag...
```

声明的 `name` 必须是单个可跨平台使用的目录名，UTF-8 编码最多 255 字节，不得包含路径分隔符、`.`、`..`、保留设备名或保留文件名字符。

限制：最多 1000 个文件，单文件 8 MiB，解压后总计 32 MiB，`SKILL.md` 最多 256 KiB，路径最多 1024 字节。归档条目必须是使用相对路径的普通文件，采用存储或 deflate 压缩，不得加密。

## 添加 skill

1. 在 Console 中打开 **技能 → 导入技能**，选择 ZIP 文件或 GitHub 仓库。
2. 打开 agent，选择导入的 skill 并保存修订版本。保存时会固定所选 skill 修订版本。
3. 使用[主环境](environments.md)开始对话，要求 agent 使用该 skill。检查它是否先读取 `SKILL.md` 再执行其中的步骤。

通过 API 使用时，skill 来源可以是上传文件或公开 GitHub 目录：

```sh
# Stage the archive (see Uploads), then create the skill from it
curl -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: release-notes-1" -F file=@release-notes.zip

curl -X POST "$A13N_URL/api/v1/skills" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"source": {"kind": "upload", "upload_id": "upl_..."}}'
```

```json
{"source": {"kind": "github", "repository": "owner/repo", "ref": "main", "path": "skills/release-notes"}}
```

- 响应的 `id`（`sk_…`）用于路径和 agent 配置。`name` 和 `description` 默认取自 `SKILL.md`；可显式提供以覆盖。
- GitHub 导入匿名读取公开仓库。`ref` 默认为默认分支，`path` 默认为仓库根目录。解析得到的 commit 会被记录；传入 `commit` 可要求精确提交，不匹配时返回 `409 conflict`，原因为 `commit_mismatch`。每次 GitHub 请求受 `control.import_timeout` 限制。
- 上传大小受 `objects.upload_bytes` 限制（默认 1 MiB）。
- `POST …/skills/validate` 传入 `{"source": ...}`，按创建时相同的规则检查软件包，返回清单（名称、描述、文件、大小、摘要和解析后的来源），不存储任何内容。

## 修订版本

- `POST …/skills/{skill_id}/revisions` 传入 `{source, note?, make_default?}` 和 skill 的 `If-Match`，添加修订版本；除非 `make_default` 为 `false`，否则设为默认修订版本。包中的 `SKILL.md` 可以声明新 `name`。包与当前默认修订版本相同时，不做修改：无论 `make_default` 如何设置，都再次返回该修订版本（`201`），不创建新的修订版本。
- `GET …/revisions` 按从新到旧列出修订版本；`POST …/revisions/{revision_id}/set-default` 修改默认修订版本。修订版本的 `skill_id` 指向所属 skill。
- `GET …/revisions/{revision_id}/content` 下载归档，`GET …/revisions/{revision_id}/files/{path}` 下载其中一个文件。
- `PATCH …/skills/{skill_id}` 修改 `name`、`description` 和 `labels`。列表可按 `label`、`q`（名称或描述）、`archived` 和 `source`（默认修订版本的 `upload` 或 `github`）筛选。
- `POST …/archive` 阻止新的 agent 修订版本固定引用该 skill，并拒绝修改；已固定引用的 agent 仍可使用。`POST …/unarchive` 取消归档。

Agent 修订版本在 `skills` 中使用 `{"skill_id": "sk_…", "revision_id": ...}` 选择 skill；不提供 `revision_id` 时，保存会固定引用 skill 当前的默认修订版本。`GET /api/v1/agents?skill_id=sk_…` 列出含有引用该 skill 的修订版本的 agent；`skill_revision_id` 可按一个 skill 修订版本筛选。

## 运行时同步

先选择并发 agent 如何使用 skill 包，再在 agent 的高级 JSON 中设置 `skill_sync`。选择 skill 或要求共享 cleanup 都需要[主环境](environments.md)。

| 需求                                | 模式                                           |
| ----------------------------------- | ---------------------------------------------- |
| Agent 使用同一组已发布的 skill 目录 | `shared`（默认）：每个名称以最后发布的内容为准 |
| Agent 在同一环境中使用不同版本      | `isolated`：目录清单使用包摘要路径             |

### 共享目录

默认配置如下：

```json
{
  "skills": [{"skill_id": "sk_…"}],
  "skill_sync": {"mode": "shared", "overwrite": true, "cleanup": false}
}
```

共享模式在 Agent 首次请求模型前，将每个选中的包发布到 `/workspace/.agents/skills/<name>`。覆盖会替换整个目录，同时移除旧版本多出的文件。设置 `overwrite: false` 保留已有名称；设置 `cleanup: true` 删除共享目录中未选中的条目，此时空选择会清空目录。共享模式不使用包缓存。

其他 run 或子 agent 读取时，共享路径仍可能变化。最后一次发布生效，与 run 完成顺序无关。多次读取可能跨越不同版本，整组 skill 也不作为一次原子更新。Cleanup 可以删除其他活跃 agent 仍需要的 skill。独立写入方选择不同 skill 时，应保留默认的 `cleanup: false`，或协调共享目录的管理权。

### 隔离包

多个 Agent 需要在同一 Environment 中使用互相冲突的版本时，选择隔离模式：

```json
{"skill_sync": {"mode": "isolated", "cache_count": 128}}
```

目录路径为 `/workspace/.agents/skill-cache/<package-digest>/<name>`，不同版本可并存，相同包复用同一目录。`cache_count` 是包数量软预算；`0` 禁用清理。每个 agent 应用自己的预算，删除空闲包并保留使用中的包。缓存可以暂时超出预算。共享模式的 cleanup 不会触碰它。

Service 以不可变方式发布包；已授权的文件和 shell 操作仍可修改它们。发布失败会保留已有包。取消或结果不确定时留下的状态，按[发布与清理](#publication-and-cleanup)处理。

### 覆盖单次运行

通过 `options.overrides.skill_sync` 为一次 run 指定策略：

```json
{"options": {"overrides": {"skill_sync": {"mode": "isolated"}}}}
```

覆盖会替换整个配置块，省略的成员取所选模式的默认值。子 Agent 使用自己的策略。`overwrite`、`cleanup` 仅适用于共享模式，`cache_count` 仅适用于隔离模式。

### 发布与清理

每个 skill 目录都从完整的暂存包发布。Linux 和 macOS 在支持的文件系统上使用原子替换，发布失败时保留旧目录。Windows 对已有目录使用普通的“删除后重命名”，优先保证可用性，同时接受可见性空档，以及替换失败时目标缺失或被部分删除的情况。其他不支持的原子替换操作失败时不改变旧目录。取消或 provider 结果不确定时，替换可能已经发布，也可能留下暂存目录。

新布局直接取代原 `.a13n/skills` 布局，不读取或迁移旧内容。缓存协调依赖 Environment 各客户端之间一致的共享文件系统操作。崩溃或结果不确定的 provider 操作可能留下暂存目录、pin 或淘汰 gate。手动回收遗留状态前，应停止所有客户端；保护状态不会按时间自动过期。
