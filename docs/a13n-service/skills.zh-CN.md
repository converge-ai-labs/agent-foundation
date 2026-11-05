---
title: Skill
description: 将工作流导入为 skill，固定到 agent，并选择文件发布方式。
---

使用 skill 为 agent 提供可复用的指令和配套文件。Agent 先看到简短的目录条目，需要时再读取相应的 `SKILL.md`。本页以发布说明 skill 为例，从本地文件开始，直到 agent 在运行中使用它。

导入 skill 需要工作空间 `write` 权限；加载导入的工作空间 skill 需要[主环境](environments.md)。工作流所需的[工具](tools.md)要单独配置。目录与按需读取行为参阅 [Harness skill](../a13n-harness/skills.md)。

## 内置技能

要让 agent 学习 Service 操作，在其技能区域选择 **platform-operations** 并保存修订版本。对应的 agent 配置片段为：

```json
{"builtin_skills": ["platform-operations"]}
```

同时启用相关[平台工具](tools.md#platform-tools)。选择 skill 只提供指令，不授予工具权限。

`GET /api/v1/builtin-skills` 列出随版本发布的内置 skill。在 Console 中打开一个条目，可以浏览 `SKILL.md`、参考文件和部署目录、查看源码，或下载 ZIP。内置 skill 随部署更新，没有工作空间 ID 或独立修订历史。平台操作包包含 `schemas/agent-config.json` 和 `instance/catalog.json`。

所有 agent（包括自定义副本和动态子 agent）都通过 `/platform-skills` 和 `/platform-instance` 的宿主只读挂载读取内置 skill，不需要主环境。只有工作空间导入的 skill 使用主环境和 `skill_sync`。宿主文件由获准的文件工具读取，不会出现在用户 shell 中。选中的内置和导入 skill 必须声明互不相同的名称。

## 软件包格式

为软件包创建一个目录，将以下文件保存为 `SKILL.md`：

```markdown title="SKILL.md"
---
name: release-notes
description: Write release notes from a supplied list of merged pull requests.
---

# Release notes

1. Read the supplied pull-request titles and descriptions.
2. Group changes under Features, Fixes, and Maintenance; omit empty groups.
3. Write one concise bullet per change and include its supplied PR number.
4. Ask for missing source details instead of inventing changes or PR numbers.
```

在该目录中使用 Python 3 创建 ZIP：

```sh
python -m zipfile -c release-notes.zip SKILL.md
```

可以加入配套文件，并从 `SKILL.md` 通过相对路径引用。将 `SKILL.md` 放在归档根目录或唯一的顶层目录中。文档必须为 UTF-8，YAML front matter 包含 `name` 和 `description`。

声明的名称用作模型可见名称和目录名。使用 `release-notes` 这类可跨平台的名称：最多 255 个 UTF-8 字节，不能包含路径分隔符、`.`、`..`、保留设备名或保留文件名字符。

| 包限制     | 上限      |
| ---------- | --------- |
| 文件数     | 1000      |
| 单文件     | 8 MiB     |
| 解压后的包 | 32 MiB    |
| `SKILL.md` | 256 KiB   |
| 相对路径   | 1024 字节 |

归档文件必须使用相对路径，采用存储或 deflate 压缩。链接和加密条目会被拒绝。上传接口还有单独的大小限制：`objects.upload_bytes`，默认 1 MiB。

## 添加 skill

1. 在 Console 中打开**技能 → 导入技能**，选择 `release-notes.zip`。也可从公开 GitHub 仓库导入目录。
2. 打开 agent，选择导入的 skill，并保存修订版本。保存时固定精确的 skill 修订版本。
3. 使用[主环境](environments.md)开始对话。提供几条已合并 PR 的描述，要求 agent 使用 `release-notes`。
4. 检查运行：agent 应读取 `SKILL.md`，并根据提供的输入生成分组的发布说明。

通过 HTTP 接入时，先[上传 ZIP](files-and-webhooks.md#uploads)，保留返回的 `upload_id`。向 `POST /api/v1/skills` 发送以下请求体，将 `upl_...` 替换为该 ID：

```json
{"source": {"kind": "upload", "upload_id": "upl_..."}}
```

也可以使用以下来源请求体，填写你的公开仓库、ref 和目录：

```json
{"source": {"kind": "github", "repository": "owner/repo", "ref": "main", "path": "skills/release-notes"}}
```

Service 匿名读取 GitHub，并记录解析后的 commit。省略 `ref` 使用默认分支，省略 `path` 使用仓库根目录。可选的 `commit` 要求解析结果精确匹配；不匹配时返回 `409 conflict`，原因为 `commit_mismatch`。每次 GitHub 请求受 `control.import_timeout` 限制。

创建前要检查来源，可将同一请求体发送到 `POST /api/v1/skills/validate`。它返回验证后的清单——名称、描述、文件、大小、摘要和解析后的来源——不存储软件包。GitHub 验证仍消耗调用方的上传预算。

保留新 skill 的 `id`（`sk_...`），用于 URL 和 agent 配置。资源名称与描述默认来自包的 front matter，也可覆盖为展示文本。模型可见名称始终来自固定版本的 `SKILL.md`；修改展示名称不会改变模型目录中的 skill 名称。

## 修订版本

更新工作流：

1. 修改包，通过 `POST /api/v1/skills/{skill_id}/revisions` 导入，传入 `{source, note?, make_default?}` 和 skill 当前的 `If-Match`。
2. 保留默认的 `make_default: true`，让新选择使用该版本；或设为 `false`，只添加修订版本而不改变默认值。
3. 保存新的 agent 修订版本，选择目标 skill 修订版本。已有 agent 修订版本保留原来的固定引用。
4. 再运行同一个小任务，检查新指令和结果。

Agent 配置在 `skills` 中用 `{"skill_id": "sk_...", "revision_id": "rev_..."}` 选择版本。省略 `revision_id` 时，在保存时固定 skill 当前默认版本。包与当前默认版本相同时，会再次返回该版本及 `201`；无论 `make_default` 如何设置，都不创建新修订版本。

| 任务                          | API                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------- |
| 按从新到旧列出修订版本        | `GET /api/v1/skills/{skill_id}/revisions`                                     |
| 更改默认版本                  | `POST /api/v1/skills/{skill_id}/revisions/{revision_id}/set-default`          |
| 下载 ZIP                      | `GET /api/v1/skills/{skill_id}/revisions/{revision_id}/content`               |
| 读取包内文件                  | `GET /api/v1/skills/{skill_id}/revisions/{revision_id}/files/{path}`          |
| 修改展示名称、描述或标签      | `PATCH /api/v1/skills/{skill_id}`                                             |
| 查找固定引用该 skill 的 agent | `GET /api/v1/agents?skill_id=sk_...`；添加 `skill_revision_id` 可限定一个版本 |

Skill 列表支持 `label`、`q`、`archived` 和 `source` 筛选；`source` 描述默认修订版本。归档 skill 会阻止新的 agent 修订版本选择它，已有固定引用仍可运行。取消归档后恢复编辑和选择。不同工作空间 skill 可以声明相同名称，但同一 agent 选中的 skill 修订版本必须声明不同名称。

## 运行时同步

<span id="shared-directories"></span> <span id="isolated-packages"></span> <span id="override-one-run"></span> <span id="publication-and-cleanup"></span>

- [Skill 发布参考](skill-publication.md)
