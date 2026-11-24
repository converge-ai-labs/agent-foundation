---
title: Skills 与 Content Plugins
description: 使用模块化操作规程（Skills）扩展 Agent 认知，并通过基于 Git 的 Content Plugin 轻松分发团队专属技能与子智能体矩阵。
---

**Skill**（技能）以包含 `SKILL.md` 指令文件的目录为单位封装可复用的操作流程。Skills 采用按需加载机制：模型先浏览技能清单中的简短描述，遇到匹配任务时才动态读取 `SKILL.md` 完整正文。

**Content Plugin**（内容插件）则进一步支持将多项 Skills 以及用 Markdown 编写的专业子智能体（Subagents）打包进标准的 Git 仓库中，方便全团队一键安装与跨项目分发。

## 开启 Skill

确保当前激活的 Agent 配置文件（位于 `agents/` 目录下）已挂载了 `skills` 能力：

```yaml title="agents/default.yaml"
capabilities:
  - capability: dynamic_environment
  - capability: skills
    configuration:
      roots: []  # 自动检索项目与用户全局目录下的技能
```

校验配置文件语法：

```bash
a13n-harness-ui config validate
```

启动终端工作台（`a13n-harness-ui`）。在输入框中键入 `$` 字符即可唤起交互式 Skill 检索面板，或直接在提示词中通过名称调用：

```text
使用 $harness-ui-configuration 帮我梳理 Model 配置文件的层级结构。
```

当任务开始时，Agent 会查阅该技能，通过内置文件工具读取完整的 `SKILL.md` 规范，并严格按照其要求组织输出。

### 编写第一个 Skill

编写专属技能只需要一个文件夹和一篇 Markdown 文件。在宿主机上创建 `~/.agents/skills/change-summary/SKILL.md`：

```markdown title="~/.agents/skills/change-summary/SKILL.md"
---
name: change-summary
description: 为 Pull Request 审查生成结构清晰、要点明确的代码变更总结。
---

在要求总结代码变更时，请严格将回答组织为以下三大模块：

1. **核心架构意图**：概述本次改动的业务背景与解决的核心痛点。
2. **关键模块调整**：按受影响的子模块以列表形式列出接口及逻辑变更。
3. **验证结果与潜在风险**：列明执行过的测试套件、边缘条件考虑及可能存在的兼容性隐患。

若传入的 Git Diff 信息不完整，应主动提问澄清，严禁臆测代码行为。
```

在 TUI 中开启新会话，输入 `$change-summary` 并附带一段 Git Diff。Agent 会立即遵循您制定的专业标准输出审查总结。

若需将技能限制在特定代码仓库内生效，只需将其保存在 `<project-root>/.agents/skills/change-summary/SKILL.md` 即可。

## 安装 Content Plugin

Content Plugin 将一系列 Skills 和 Markdown 子智能体打包在 Git 仓库中统一分发。

1. 获取包含有效插件描述清单的 Git 仓库地址。
2. 使用 CLI 命令行工具一键安装：

```bash
a13n-harness-ui plugin install https://github.com/example-org/team-agent-plugins.git
```

如果在插件本地开发过程中进行测试，直接传入本地文件系统路径即可：

```bash
a13n-harness-ui plugin install /path/to/my-plugin-repo
```

检查已安装的插件清单：

```bash
a13n-harness-ui plugin list --format json
```

安装完成后，插件内贡献的所有技能均会立即出现在 TUI 与 WebUI 的 `$` 自动补全菜单中。

## 使用插件提供的 subagent

Content Plugin 除了贡献规程外，还可以携带专业子角色定义（如安全审计员、技术文档撰写人等）。要将插件中的子角色挂载到您的主 Agent 中，只需在 Agent YAML 中引用其 ID：

```yaml title="agents/default.yaml"
subagents:
  - markdown: subagent-security-reviewer
```

执行 `a13n-harness-ui config validate` 校验配置。主 Agent 即可在任务规划时将专业事务委派给该子智能体。

## 移除 Content Plugin

当不再需要某个插件或需要清理本地缓存时，执行卸载命令：

```bash
a13n-harness-ui plugin uninstall plugin-review
```

卸载后，确认该插件已从 `a13n-harness-ui plugin list` 列表中移除。

## 编写 Content Plugin 仓库

创建可供团队分发的 Content Plugin 仓库只需编写三个标准文件：

1. 在 Git 仓库根目录下创建 `.agents/plugins/marketplace.yaml`：

```yaml title=".agents/plugins/marketplace.yaml"
schema_version: "1"
name: 团队标准工作流集合
plugins:
  - path: ./plugins/engineering
```

2. 在插件子目录下创建清单文件 `plugins/engineering/.a13n-plugin/plugin.yaml`：

```yaml title="plugins/engineering/.a13n-plugin/plugin.yaml"
schema_version: "1"
kind: content_plugin
id: plugin-engineering-tools
name: 工程规范技能包
version: "1.0.0"
description: 提供工程代码审查、安全审计子智能体与发布流程标准操作规程。
skills: ./skills
subagents: ./subagents
```

3. 将技能文件存放在 `plugins/engineering/skills/` 目录下（如 `skills/change-summary/SKILL.md`），将子智能体 Markdown 文件放置在 `plugins/engineering/subagents/` 目录下。

提交至 Git 仓库并将仓库链接分享给团队成员，即可实现技能的无缝同步。

### Marketplace 字段

| 字段名           | 作用说明                   | 约束要求             |
| :--------------- | :------------------------- | :------------------- |
| `schema_version` | 描述文件格式版本           | 固定填写 `"1"`       |
| `name`           | 插件集合名称               | 1–256 个字符         |
| `plugins`        | 包含的插件相对目录路径列表 | 至少包含一个插件目录 |

### Manifest 字段

| 字段名           | 作用说明                           | 约束要求                      |
| :--------------- | :--------------------------------- | :---------------------------- |
| `schema_version` | 清单契约规范版本                   | 固定填写 `"1"`                |
| `kind`           | 插件类型标记                       | 固定为 `content_plugin`       |
| `id`             | 全局唯一的插件标识符（连字符小写） | 如 `plugin-engineering-tools` |
| `name`           | 在界面菜单中展示的友好名称         | 1–256 个字符                  |
| `version`        | 语义化版本号                       | 如 `"1.0.0"`                  |
| `description`    | 插件功能描述摘要                   | 1–4096 个字符                 |
| `skills`         | 包含 Skills 的相对子目录路径       | 若包含 subagents 则可选       |
| `subagents`      | 包含 Subagents 的相对子目录路径    | 若包含 skills 则可选          |

## 自动来源与优先级

当不同路径下存在同名的 Skill 技能时，Harness UI 会采用明确的确定性优先级覆盖策略：

1. `capabilities.skills.configuration.roots` 中显式指定的绝对路径（最高优先级）。
2. 当前项目工作空间目录：`<project-root>/.agents/skills/`。
3. 本地已安装的 Content Plugins（按安装顺序检索）。
4. 用户全局目录：`~/.agents/skills/`。
5. 系统内置配置技能（最低优先级）。

这种层级设计确保了项目专属的操作规范可以自然覆盖用户个人或系统全局的通用默认规程。

## 显式 Skill 根目录

如果需要挂载存放在非标准路径下的技能库，可以在 Agent 的配置中添加显式根目录：

```yaml
capabilities:
  - capability: skills
    configuration:
      roots:
        - /shared/enterprise/team-skills
```

Harness UI 会在启动时自动对其建立索引，并将包含的技能合并进候选目录中。

## 内置配置 Skill

Harness UI 开箱内置了名为 `harness-ui-configuration` 的官方技能。该技能直接打包随二进制发布，完全离线可用，无需外部网络支持。

当 Agent 在规划任务中需要准确理解模型提供方参数配置、调优 YAML 语法或检索系统能力说明时，只需在会话中输入 `$harness-ui-configuration`，Agent 便能自动翻阅当前版本权威的手册索引。

## 用户与插件文件挂载

启用 Skills 后，Harness UI 会自动将 `~/.agents/skills` 以只读模式暴露给 Agent。这赋予了 Agent 阅读操作规程的必要权限，同时严密保护了用户的整个家目录不被随意遍历。已安装的 Content Plugin 同样通过安全的独立只读挂载点运作。

## 排查发现问题

若自定义技能未在自动补全候选单中出现，请按以下顺序排查：

1. **检查 Capability 声明：** 确认当前激活的 Agent 配置文件中包含 `capability: skills`。
2. **运行配置校验：** 运行 `a13n-harness-ui config validate` 检查是否有 YAML 格式错误。
3. **确认文件与目录命名：** 确保技能文件名严格大写为 `SKILL.md`，且放置在命名的独立文件夹中（例如 `my-skill/SKILL.md`）。
4. **比对覆盖优先级：** 若被其他同名技能遮蔽，对照上方的优先级次序确认生效的加载目录。
