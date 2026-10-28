---
title: Skill 与 Content Plugin
description: 启用 Skill、编写可复用流程，并通过 Content Plugin 共享 Skill 和子角色。
---

Skill 是包含 `SKILL.md` 的目录，Agent 会在任务需要时阅读其中的指引。先启用 Skill 并试用内置配置指南，再添加自己的本地流程，或通过基于 Git 的 Content Plugin 分发。

## 开启 Skill

使用已经能够正常回答且能读取文件的 Agent，例如[最小文件配置 Agent](agents-and-subagents.md#create-an-agent-from-files)。

1. 打开所选根 YAML 旁 `agents/` 中的 Agent 文件。在已有 `capabilities` 列表中加入 `skills`，保留 `dynamic_environment` 和其他条目。以下是合并片段，不是完整 Agent 文件：

   ```yaml
   capabilities:
     - capability: dynamic_environment
     - capability: skills
       configuration:
         roots: []
   ```

2. 验证同一配置树：

   ```console
   a13n-harness-ui config validate
   ```

   自定义配置树需在 `config` 前加 `--config /absolute/path/a13n-harness-ui.yaml`。处理报告的错误或 Capability 警告。

3. 选择该 Agent 并开始新 Run。在 TUI 或 WebUI 输入框中输入 `$`，选择 `harness-ui-configuration`，也可以输入完整名称：

   ```text
   Use $harness-ui-configuration to explain where my Model files belong. Do not change files.
   ```

4. 检查工具活动：Agent 应先读取所选 `SKILL.md`，再使用其中的指引。仅有回答不能证明文件已被读取。

`roots: []` 启用自动来源。移除 `skills` Capability 会关闭该 Agent 的发现功能。Skill 提供指引；文件访问、工具权限和 Model 身份验证仍由 Agent 与 Environment 提供。

### 编写第一个 Skill

在 Harness UI Host 上创建 `~/.agents/skills/change-summary/SKILL.md`。下面的完整文件定义了一个流程，用于总结提示中提供的变更：

```markdown title="~/.agents/skills/change-summary/SKILL.md"
---
name: change-summary
description: Summarize a supplied code change for a reviewer.
---

Read the supplied change and explain:

1. What behavior changed.
2. Which checks ran and what they established.
3. What the reviewer still needs to verify.

Ask for missing change details instead of inventing them.
```

用同一 Agent 开始新 Run。输入 `$change-summary`，随后提供一段简短的变更说明和测试结果。检查 Agent 是否读取了文件，并按三个部分组织回答。

仓库共享的 Skill 则保存到 `<project-root>/.agents/skills/` 下。使用 Thread 实际选中的本地根目录；配置目录不是自动 Project Skill 来源。

## 安装 Content Plugin

Content Plugin 将 Skill 和 Markdown 子角色作为可编辑文件共享。它不会安装可执行的 [Harness Plugin](extensions-and-mcp.md#harness-plugin-and-run-extension-files)。

1. 获取可信的 Git 仓库，其中应包含 `.agents/plugins/marketplace.yaml` 和插件 manifest。只有 `SKILL.md` 的目录是本地 Skill，不是插件仓库。

2. 从本地 Git 路径安装，将下列路径替换为实际仓库：

   ```console
   a13n-harness-ui plugin install /absolute/path/to/plugin-repository
   a13n-harness-ui plugin list --format json
   ```

   仓库有多个有效插件时，加上 `--plugin plugin-review` 并使用目标 ID。远程 Git 仓库用 URL 替代本地路径。添加 `--ref` 可选择已有 tag 或 commit。

3. 检查列表中的插件 ID、安装路径和 commit。安装会将选中内容复制到 `<data-root>/content-plugins/<plugin-id>` 并记录来源。

4. 使用已启用 Skill 的 Agent 开始新 Run。通过 `$` 选择插件提供的 Skill，检查 Agent 是否读取了安装后的文件。

这些命令应与应用使用相同的 `--config` 和 `--data-root`。已有插件 ID 不会被覆盖。安装后的文件可以本地编辑，但不会持续与 Git 同步，也没有 `plugin update` 命令。

## 使用插件提供的 subagent

安装插件后，其子角色可供选择。要加入角色，将该 ID 合并到父 Agent 的 `subagents` 列表：

```yaml
subagents:
  - markdown: subagent-investigator
```

使用插件实际提供的 ID。运行 `config validate`，选择父 Agent 并开始新 Run。先检查子角色清单，再要求 Agent 委派任务。Model 继承与工具过滤见 [Agent 与 subagent](agents-and-subagents.md)。

本地 `subagents/*.md` 会覆盖同 ID 的插件子角色。插件之间的冲突按确定性顺序处理并产生诊断。内置子角色 ID 是保留值。选中的子角色缺失时，组合会失败，不会替换为其他角色。

## 移除 Content Plugin

先备份本地修改，并移除 Agent 对插件子角色的引用。**卸载会删除安装目录及其中的本地编辑，不会再次确认。** 然后运行：

```console
a13n-harness-ui plugin uninstall plugin-review
a13n-harness-ui plugin list --format json
```

将 `plugin-review` 替换为已安装 ID，检查列表中已无该项。安装后的 manifest 损坏时也可以卸载。重新安装只复制仓库内容，不恢复已删除的修改；恢复旧 Run 也不会重建目录。

## 编写 Content Plugin 仓库

用 Git 仓库打包前面的 `change-summary` Skill。以下所有路径都相对于仓库根目录。

1. 创建完整的 marketplace 文件：

   ```yaml title=".agents/plugins/marketplace.yaml"
   schema_version: "1"
   name: Team content
   plugins:
     - path: ./plugins/review
   ```

2. 创建完整的插件 manifest：

   ```yaml title="plugins/review/.a13n-plugin/plugin.yaml"
   schema_version: "1"
   kind: content_plugin
   id: plugin-review
   name: Review workflows
   version: "1.0.0"
   description: Shared review Skills.
   skills: ./skills
   ```

3. 将前面的 Skill 文件保存到 `plugins/review/skills/change-summary/SKILL.md`。manifest 中的 `./skills` 相对于 `plugins/review`，不是仓库根目录。

4. 在源仓库中提交这些文件。安装会克隆 Git 内容，未提交的工作区修改不会成为安装来源。

5. 按[安装 Content Plugin](#install-a-content-plugin)操作，使用该仓库路径。确认安装的 commit，并在新 Run 中测试 `$change-summary`。

如果还要提供子角色，在 manifest 中加入 `subagents: ./subagents`，将[规范 Markdown 角色](agents-and-subagents.md#write-a-markdown-subagent)放到 `plugins/review/subagents/`。

### Marketplace 字段

| 字段             | 要求                                  |
| ---------------- | ------------------------------------- |
| `schema_version` | 必需，`"1"`                           |
| `name`           | 必需，1–256 个字符                    |
| `plugins`        | 必需，1–256 个条目，路径唯一          |
| `plugins[].path` | 规范的仓库相对插件目录，3–4096 个字符 |

### Manifest 字段

| 字段             | 要求 / 默认值                                                           |
| ---------------- | ----------------------------------------------------------------------- |
| `schema_version` | 必需，`"1"`                                                             |
| `kind`           | 必需，`content_plugin`                                                  |
| `id`             | 必需的 `plugin-` ID；小写 ASCII 字母/数字以单个连字符分隔；8–128 个字符 |
| `name`           | 必需，1–256 个字符                                                      |
| `version`        | 必需的非空标签，最多 128 个字符                                         |
| `description`    | 必需，1–4096 个字符                                                     |
| `skills`         | 可选的相对 Skill 目录                                                   |
| `subagents`      | 可选的相对 Markdown 角色目录                                            |

`skills` 与 `subagents` 至少需要一个。路径使用 `./...`，须留在所属目录内，不能使用路径遍历或符号链接。未知字段会被拒绝。安装执行大小、文件类型、Git 操作和 YAML 解析器限制。`.a13n-plugin/origin.json` 单独记录仓库和 commit 来源，不与可编辑 manifest 混合；版本标签不会触发更新。

## 自动来源与优先级

Skill 名称相同时，优先级最高的可用来源生效：

1. 显式 `roots`，后面的条目优先。
2. 所选 Device 工作目录的 `.agents/skills`，后面的绑定优先。
3. 第一个本地根目录的 `.agents/skills`。
4. 后续本地根目录的 `.agents/skills`，按选择顺序。
5. 已安装 Content Plugin 的 Skill 根目录，字典序更靠后的插件 ID 优先。
6. `~/.agents/skills` 中的用户 Skill。
7. 内置挂载中由发行版提供的 Skill。

没有本地根目录时，对应层级消失。Device 来源仍可通过 `/environment/<alias><device-cwd>/.agents/skills` 使用。

每个 Run 准备自己的目录。新增或重命名 Skill 后，开始新 Run 以重新发现；活动 Run 保留原目录。缺失的可选目录不提供 Skill，显式根目录不可用则准备失败。文件在使用时读取，因此捕获路径不会固定文件内容。

输入完整且已识别的 `$name` 与选择补全项效果相同。新提交按当前目录解析；引导使用活动 Run 的目录。未知名称保持普通文本，草稿保留可编辑的 `$name`。程序化 Skill 引用有单独的验证；目录预览不会预留后续 Run 的目录。

## 显式 Skill 根目录

自动约定之外、但已能通过所选 Environment 访问的目录，可以设为显式根目录。将以下条目合并到 Agent 的 Capability 列表，并替换路径：

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /absolute/mounted/project/team-skills
```

`roots` 最多接受 128 个有序、唯一、规范的绝对 Environment 路径，用于补充自动来源。保留 Host 路径的布局使用已挂载的 Host 路径；虚拟布局使用 `/workspace/team-skills` 等路由。相对路径、`~` 和 Provider 内部路径无效。列出目录不会挂载它；准备目录时，每个显式根目录都必须能解析。

## 内置配置 Skill

`harness-ui-configuration` 包含与发行版匹配的文档和页面/章节索引，可从以下只读挂载离线使用：

```text
/environment/builtin-skills/harness-ui-configuration
```

这是非默认、仅文件挂载，支持检查、读取、搜索和复制源操作。启用 Skill 后，即使没有 Project，或 Project 是远程、沙箱环境，也能使用。它不增加配置写入特权，也不会将文件复制到用户目录。更高优先级 Skill 可覆盖同名项；索引中的在线参考可能描述其他发行版。

源码开发时，修改文档或导航后，从 `docs/a13n-harness-ui/` 重新生成内置副本：

```console
make a13n-harness-ui-skills
```

`make a13n-harness-ui` 包含此步骤。编辑源文档，不要编辑生成的 Skill 树。发布的 wheel 和 sdist 已包含 Skill；从 sdist 重建 wheel 不需要仓库文档树或 Node.js。

## 用户与插件文件挂载

选择 Skill 后，Harness UI 通常将确切的 `~/.agents/skills` 暴露为可写、仅文件的用户挂载；不存在时创建目录。它不授予 shell 或整个主目录访问权。如果已有保留 Host 路径且完全匹配的 Project 根目录，则可复用，避免重复挂载。

已安装插件目录各自拥有可编辑的文件挂载。挂载允许 Agent 检查内容；是否发现 Skill 由所选 `skills` Capability 控制，是否加入子角色由父 Agent 的角色清单控制。

## 排查发现问题

依次检查：

1. **补全中没有 Skill：** 确认所选 Agent 包含 `skills`，验证配置并开始新 Run。
2. **缺少自己的 Skill：** 检查 Thread 实际根目录、`SKILL.md` 位置与 front matter，以及 Environment 路径。在 `<data-root>/logs/terminal.log` 查看跳过条目的诊断。
3. **同名 Skill 选错来源：** 对照上面的优先级列表检查其来源。
4. **缺少插件 Skill 或子角色：** 运行 `plugin list --format json`，先检查已安装 manifest 中的路径和 Agent 选中的子角色 ID，再考虑重新安装。
5. **列出了 Skill 却无法使用：** 检查文件工具是否可见，以及 Environment 权限。选择 Skill 不会授予这两者。
