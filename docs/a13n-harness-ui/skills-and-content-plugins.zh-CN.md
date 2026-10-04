---
title: Skill 与 Content Plugin
description: 开启 Skill，并安装通过 Git 共享 Skill 和 subagent 角色的 Content Plugin。
---

Skill 提供操作指引和参考文件。Content Plugin 在 Git 仓库中分发可编辑 Skill 和 Markdown subagent 角色。两者都不会安装可执行 Harness Plugin、授予工具权限或替代 Model 身份验证。

## 开启 Skill

在 Agent 已有的 `capabilities` 列表中加入：

```yaml
capabilities:
  - capability: skills
    configuration:
      roots: []
```

空列表仍启用自动来源；省略该 Capability 会关闭此次 Agent Run 的 Skill 发现。编辑时保留其他 Capability 条目。

在 TUI 或 WebUI 输入框中输入 `$` 可发现 Skill 名称。例如，让 Agent 使用 `$harness-ui-configuration` 检查配置。识别出的名称仍作为提示文本可见，并携带 App 在发送或引导前验证的引用。旧引用按名称在当前目录解析；引导使用活动 Run 固定的目录。未知的美元符号前缀文本仍是普通文本。引用请求使用该 Skill，不会捕获内容或增加权限。不存在 `/skill-name` 命令。

## 自动来源与优先级

两个来源使用相同 Skill 名称时，以下优先级列表中首个可用来源生效：

1. 显式 `roots`，后面的条目优先于前面的条目。
2. 第一个本地根目录的 `.agents/skills`。
3. 后续本地根目录的 `.agents/skills`，按选择顺序。
4. 已安装 Content Plugin 的 Skill 根目录；字典序更靠后的插件 ID 优先。
5. `~/.agents/skills` 中的用户 Skill。
6. 内置挂载中由发行版提供的 Skill。

没有本地根目录时，仅两个本地根目录层级消失。内置、用户、插件和显式来源仍可用。配置目录不会隐式成为 Project Skill 来源。

目录在 Run 准备时选定。新 Run 捕获可以看到来源变化；活动 Run 不会重新扫描挂载。缺失的可选目录直接视为不存在，显式根目录不可用则导致准备失败。Skill 文件在使用时读取，不会复制到 Run 中。

## 显式 Skill 根目录

`configuration.roots` 接受最多 128 个有序、唯一、规范的绝对 **Environment 路径**。这些根目录补充自动来源，不会替代它们。

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /absolute/mounted/project/team-skills
```

使用选中 Environment 暴露的路径。不要使用 `~`、相对路径或 Provider 内部路径。保留 Host 路径的布局使用挂载后的规范 Host 路径；虚拟布局使用 `/workspace/team-skills` 等路由。改变布局时可能需要更新显式根目录。

在这里列出 Host 目录不会挂载它，也不能绕过 Sandbox 策略。准备目录时，每个必需根目录都必须能够解析。

## 内置配置 Skill

wheel 和 sdist 包含 **`harness-ui-configuration`**，提供与发行版匹配的 Harness UI 文档和生成的页面/章节索引。它可离线使用；运行时发现不会下载网站或重新生成文档。

只读 `builtin-skills` 挂载使用稳定路径：

```text
/environment/builtin-skills/harness-ui-configuration
```

该挂载不是默认挂载，仅提供文件操作：检查、读取、搜索和复制源；不提供修改、shell、进程、端口或输出操作。Agent 选择 `skills` 时，没有 Project、Project Provider 为沙箱或远程时仍可用。不会将文件复制到用户配置目录、Project 或 `~/.agents/skills`。

该 Skill 来源优先级最低，可通过更高优先级来源的同名 Skill 覆盖。其指令仍使用普通文件操作和验证，不增加配置写入特权。章节索引中的外部引用标为在线内容，可能描述其他发行版。

源码开发时，修改 Harness UI 文档或导航后重建内置 Skill：

```console
make a13n-harness-ui-skills
```

`make a13n-harness-ui` 已依赖此步骤。生成副本的唯一来源为 `docs/a13n-harness-ui/`；不要独立编辑生成的 Skill 树。从 sdist 重建 wheel 不需要仓库文档树或 Node.js。

## 用户与插件文件挂载

选择 Skill 通常会将确切的 `~/.agents/skills` 目录作为新的用户文件挂载暴露，目录不存在时创建。用户拥有该目录，因此可写，但不会授予 shell 或整个用户主目录的访问权限。如果 Project 根保留 Host 路径且完全匹配，可复用它，避免创建含糊的重复挂载。

已安装 Content Plugin 目录分别挂载，便于检查或编辑内容。可用不代表每个贡献的 Skill 都被选中，也不代表每个子角色都已加入。删除已安装目录会真正删除内容；恢复旧捕获不会重建它。

## 安装 Content Plugin

插件来源必须是含 marketplace 和选中插件 manifest 的 Git 仓库，不能只是一个含 `SKILL.md` 的目录：

```console
a13n-harness-ui plugin install /path/to/plugin-repository
a13n-harness-ui plugin install https://github.com/example/agent-content --plugin plugin-review --ref v1.0.0
a13n-harness-ui plugin list --format json
```

使用已有仓库/ref 和插件 ID。如果 marketplace 恰好有一个有效插件，可以省略 `--plugin`；否则需选择一个。安装会记录仓库/提交来源，并在 `<data-root>/content-plugins/<plugin-id>` 发布完整本地目录。

不会覆盖已安装的 ID。没有 `plugin update` 命令。已安装文件是可编辑的普通内容，不是持续同步的检出目录。安装前检查并确认信任来源；验证不能保证指令安全。

## 移除 Content Plugin

**卸载会立即删除安装目录，包括你的本地编辑，不会再次确认。** 运行前备份需要保留的修改：

```console
a13n-harness-ui plugin uninstall plugin-review
```

重新安装只会复制仓库内容，不会恢复已删除的本地修改。即使 manifest 无效，也可以卸载损坏安装。移除来源可能让显式选择的子级在后续组合中不可用。

## 使用插件提供的 subagent

在 Agent 上像其他 Markdown 角色一样选择插件的 Markdown 子角色：

```yaml
subagents:
  - markdown: subagent-investigator
```

仅安装不会加入所有子角色。本地 `subagents/*.md` 覆盖同 ID 的插件子角色。插件之间的冲突按确定性顺序处理，并生成诊断。包提供的内置子级 ID 为保留值。选中的子级缺失会导致组合失败；无效可选内容不会静默转换为其他角色。

Model 继承、工具过滤和角色清单规则见 [Agent 与 subagent](agents-and-subagents.md)。

## 编写 Content Plugin 仓库

在仓库根创建 **`.agents/plugins/marketplace.yaml`**：

```yaml
schema_version: "1"
name: Team content
plugins:
  - path: ./plugins/review
```

创建 **`plugins/review/.a13n-plugin/plugin.yaml`**：

```yaml
schema_version: "1"
kind: content_plugin
id: plugin-review
name: Review workflows
version: "1.0.0"
description: Shared review Skills and child roles.
skills: ./skills
subagents: ./subagents
```

声明的两个目录相对于该插件根目录。在 `skills/` 下每个 Skill 的独立子目录中放置 `SKILL.md`；在 `subagents/` 下放置规范 Markdown 角色。

### Marketplace 字段

| 字段             | 要求                                                  |
| ---------------- | ----------------------------------------------------- |
| `schema_version` | 必需，值为 `"1"`                                      |
| `name`           | 必需，1–256 个字符                                    |
| `plugins`        | 必需，1–256 个条目，路径唯一                          |
| `plugins[].path` | 必需，指向插件根目录的规范仓库相对路径，3–4096 个字符 |

### Manifest 字段

| 字段             | 要求 / 默认值                                                                |
| ---------------- | ---------------------------------------------------------------------------- |
| `schema_version` | 必需，值为 `"1"`                                                             |
| `kind`           | 必需，值为 `content_plugin`                                                  |
| `id`             | 必需，`plugin-` ID，由小写 ASCII 字母/数字及单个分隔连字符组成，8–128 个字符 |
| `name`           | 必需，1–256 个字符                                                           |
| `version`        | 必需，非空版本标签，最多 128 个字符                                          |
| `description`    | 必需，1–4096 个字符                                                          |
| `skills`         | 可选相对目录，默认不存在                                                     |
| `subagents`      | 可选相对目录，默认不存在                                                     |

`skills` 和 `subagents` 至少需要一项。声明路径使用 `./...`，必须留在所属目录内，不能通过路径遍历或链接逃逸。未知字段会被拒绝。版本标签用于记录来源，不是自动更新 resolver。

安装会执行大小、文件类型、Git 操作和 YAML 解析器限制，不接受任意仓库内容。`.a13n-plugin/origin.json` 与可编辑 manifest 分开记录来源。

## 排查发现问题

使用 `config validate`、`config show`、`plugin list` 和 `<data-root>/logs/terminal.log` 查看诊断。重新安装前，先检查 Agent 的 Capability 选择、实际 Environment 路径、来源优先级、manifest 路径和选中子级 ID。

Skill 目录预览成功不会固定后续 Run 的目录，也不会捕获 Skill 字节。精确的嵌入 API Skill 引用会单独验证；普通美元符号文本不提供此保证。
