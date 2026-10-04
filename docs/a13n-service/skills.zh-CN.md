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
