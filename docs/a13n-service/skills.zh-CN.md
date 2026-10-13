---
title: Skill
description: 将指令和文件打包为带修订版本的 skill，供 agent 按需加载。
---

Skill 是一组指令和配套文件，agent 在任务需要时可以加载。Skill 使用[修订版本](resources.md#lifecycles)管理：每个新软件包新增一个不可变的修订版本，agent 修订版本固定引用精确的 skill 修订版本。运行时的使用方式请参阅 [Harness skill](../a13n-harness/skills.md)。

Skill 通过 [ID](resources.md#common-conventions)（`sk_…`）标识，用于 `/api/v1/skills/{skill_id}` 等路径和 agent 配置。模型看到的名称来自固定修订版本的 `SKILL.md` 中声明的 `name`。同一工作空间的两个 skill 可以声明相同名称，新修订版本也可以改名，但同一 agent 的 skill 必须声明互不相同的名称。

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

在 Console 中打开 **技能 → 导入技能**，选择 ZIP 文件或 GitHub 仓库。通过 API 使用时，skill 来源可以是上传文件或公开 GitHub 目录：

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

在 Agent 配置中用一个 `skill_sync` 块管理所有选中的 skill（Console 中使用高级 JSON）。默认采用共享模式，后发布的内容生效：

```json
{
  "skills": [{"skill_id": "sk_…"}],
  "skill_sync": {"mode": "shared", "overwrite": true, "cleanup": false}
}
```

共享模式在 Agent 首次请求模型前，将每个选中的包发布到 `/workspace/.agents/skills/<name>`。覆盖会替换整个目录，同时移除旧版本多出的文件。设置 `overwrite: false` 保留已有名称；设置 `cleanup: true` 删除共享目录中未选中的条目，此时空选择会清空目录。共享模式不使用包缓存。

每个 skill 目录都从完整的暂存包发布。Linux 和 macOS 在支持的文件系统上使用原子替换，发布失败时保留旧目录。Windows 对已有目录使用普通的“删除后重命名”，优先保证可用性，同时接受可见性空档，以及替换失败时目标缺失或被部分删除的情况。其他不支持的原子替换操作失败时不改变旧目录。取消或 provider 结果不确定时，替换可能已经发布，也可能留下暂存目录。

其他 run 或子 agent 读取时，共享路径仍可能变化。最后一次发布生效，与 run 完成顺序无关。多次读取可能跨越不同版本，整组 skill 也不作为一次原子更新。Cleanup 可以删除其他活跃 agent 仍需要的 skill。独立写入方选择不同 skill 时，应保留默认的 `cleanup: false`，或协调共享目录的管理权。

多个 Agent 需要在同一 Environment 中使用互相冲突的版本时，选择隔离模式：

```json
{"skill_sync": {"mode": "isolated", "cache_count": 128}}
```

目录中的路径为 `/workspace/.agents/skill-cache/<package-digest>/<name>`。不同包并存，归档字节相同的包复用目录，无需再次下载。`cache_count` 是该 Environment 的非负包数量软预算；`0` 禁用清理。每个 Agent 在物化时应用自身的预算。使用中的包受到 pin 保护，执行结束时释放；清理按最近使用时间淘汰空闲包，单次工作量有界，因此缓存可能暂时超出预算。共享模式的 cleanup 不会触碰这个缓存。

隔离模式共享不可变的包内容，不为每个 agent 分配物理副本。不可变性是 Service 的发布保证；已授权的文件和 shell 操作仍可修改这些文件。缓存发布失败不会改变已有包。取消或结果不确定的修改会保留防止淘汰的保护状态，也可能留下暂存目录或 pin，供运维人员后续清理。

| 场景                                | 结果                                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------ |
| 共享模式，不同名称，关闭 cleanup    | 两个名称并存。                                                                       |
| 共享模式，相同名称和相同包          | 覆盖会重新发布相同内容。                                                             |
| 共享模式，相同名称、不同包          | 最后一次发布生效，已在运行的读取方也可能看到新版本；Windows 接受删除后重命名的空档。 |
| 共享模式，`overwrite: false`        | 保留已有名称或并发发布的胜出者，即使它与选中的修订版本不同。                         |
| 共享模式，`cleanup: true`           | 删除未选中的名称，包括其他 agent 正在使用的内容；空选择清空共享根目录。              |
| 隔离模式，相同包字节                | 不同 agent 和修订版本复用同一目录。                                                  |
| 隔离模式，同一名称的不同版本        | 各自的目录清单指向不同摘要路径，两个版本并存。                                       |
| 隔离模式，缓存超出预算              | 活跃包继续受到 pin 保护；有界清理按最近使用时间淘汰空闲包。                          |
| 同一 environment 混用共享和隔离模式 | 根目录分离，共享覆盖和 cleanup 不影响隔离缓存。                                      |

通过 `options.overrides.skill_sync` 为一次 run 指定策略：

```json
{"options": {"overrides": {"skill_sync": {"mode": "isolated"}}}}
```

覆盖会替换整个配置块，省略的成员取所选模式的默认值。子 Agent 使用自己的策略。`overwrite`、`cleanup` 仅适用于共享模式，`cache_count` 仅适用于隔离模式。选择 skill 或要求共享 cleanup 都需要主 Environment。

新布局直接取代原 `.a13n/skills` 布局，不读取或迁移旧内容。缓存协调依赖 Environment 各客户端之间一致的共享文件系统操作。崩溃或结果不确定的 provider 操作可能留下暂存目录、pin 或淘汰 gate。手动回收遗留状态前，应停止所有客户端；保护状态不会按时间自动过期。
