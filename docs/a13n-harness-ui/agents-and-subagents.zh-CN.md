---
title: Agent 与 subagent
description: 创建 Agent，在仓库中运行，并添加继承或独立选择 Model 的辅助角色。
---

**Agent** 保存 Model 选择、指令、工具和可选的辅助角色。先创建并测试一个 Agent，再根据具体委派任务添加 subagent。

| 任务                        | 从这里开始                                                   |
| --------------------------- | ------------------------------------------------------------ |
| 交互式创建 Agent            | `a13n-harness-ui add agent`                                  |
| 用 YAML 创建 Agent          | [从文件创建 Agent](#create-an-agent-from-files)              |
| 添加包自带的辅助角色        | [内置 subagent](#built-in-subagents)                         |
| 编写使用父级 Model 的角色   | [编写 Markdown subagent](#write-a-markdown-subagent)         |
| 为角色配置独立 Model 或工具 | [引用已有 Agent](#reference-an-existing-agent-as-a-subagent) |
| 查阅配置字段                | [Agent 文件参考](#agent-file-reference)                      |

## 从文件创建 Agent

此例创建带文件和 shell 工具的编码 Agent。需要已安装的 Harness UI 和可用的 Model 连接；尚未准备好时，先完成[设置](setup.md)。

运行 `a13n-harness-ui config path`。以下路径均相对于 `configuration_path` 所示根 YAML 的父目录，通常是 `~/.a13n-harness-ui/`。使用自定义配置树时，在每个命令的子命令前加 `--config /absolute/path/a13n-harness-ui.yaml`。

### 1. 创建 Model

如果设置流程已创建 Model，在第 2 步复用其 `id`。否则，将以下完整文件保存为 `models/primary.yaml`：

```yaml title="models/primary.yaml — complete file"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

在启动 Harness UI 的进程中设置 `OPENAI_API_KEY`。YAML 保存变量名，不保存密钥。使用已存密钥或订阅连接时，参见 [Model 与身份验证](models-and-authentication.md)。

### 2. 创建根 Agent

将以下完整文件保存为 `agents/coder.yaml`。如果复用其他 Model，请替换 `model-primary`。

```yaml title="agents/coder.yaml — complete file"
schema_version: "1"
kind: agent
id: agent-coder
name: Coding agent
model: model-primary
instructions: |
  Make focused changes. Read the relevant code first, validate the changed
  behavior, and report the result.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
subagents: []
```

`dynamic_environment` 提供所选 Environment 支持的文件和 shell 工具。Environment 访问规则和根级工具开关仍然生效。默认 Full Control 配置以主机账户权限执行；需要隔离时，选择 [Sandbox 或自定义 Environment](environments-and-projects.md)。

先使用这个小配置。Agent 有相应需求后，再添加 [Skill](skills-and-content-plugins.md) 或其他 [Capability](extensions-and-mcp.md)。Harness UI 会提供应用基础设施，包括已配置的任务和提问工具。

### 3. 选择并验证

验证配置，然后从仓库目录启动新对话：

```console
a13n-harness-ui config validate
cd /absolute/path/to/your-repository
a13n-harness-ui --agent agent-coder
```

发送：“列出顶层文件，说明源代码放在哪里。不要修改文件。”检查回复是否包含来自目标仓库的文件工具结果。`/status` 显示所选 Agent 和 Model。验证成功表示配置通过检查；这条提示词才会实际使用 Model 和 Environment 连接。

要将该 Agent 设为新对话的默认值，将以下字段合并到根 YAML，并保留其他设置：

```yaml title="a13n-harness-ui.yaml — merge into existing defaults"
defaults:
  agent: agent-coder
```

`--agent` 用于新对话，不能与 `--resume` 同用。常见单目录流程中，启动目录提供 Project 和工作目录，无须单独创建 Project 文件。显式多根 Project 参见 [Project 与 Thread](environments-and-projects.md)。

## 内置 subagent

包内提供三个辅助角色。在**根 YAML** 中选择，不要把这些名称直接放进 Agent 的 `subagents` 列表。

| 名称            | 适合交给它的任务                       |
| --------------- | -------------------------------------- |
| `explorer`      | 定位具体实现，返回文件引用             |
| `code-reviewer` | 审查指定修改，报告实质缺陷或无问题结果 |
| `executor`      | 完成范围明确、预期结果清楚的任务       |

1. 将以下片段合并到 `a13n-harness-ui.yaml`：

   ```yaml title="a13n-harness-ui.yaml — merge fragment"
   subagents:
     include: [explorer, code-reviewer]
   ```

2. 查看目录并验证：

   ```console
   a13n-harness-ui config subagents
   a13n-harness-ui config validate
   ```

3. 用 `agent-coder` 启动新对话。让它将一个范围较小的仓库问题委派给 `explorer`。用 `/subagents` 查看子级执行及结果。

常规设置会选择全部三个角色。省略 `include` 或将其设为 `[]`，可关闭自动包含。`setup --advanced` 提供全选或不选；选择部分角色时编辑列表。

内置角色继承父级的 Model、Capability 和可见工具。角色指令不会使辅助角色自动变成只读。需要限制文件工具时，使用下一节的显式过滤器。

所选角色按列表顺序追加在根 Agent 自己声明的子级之后。它们是叶节点；自动包含不会递归附加到每个子 Agent。要只为某个 Agent 添加一个包内角色，在该 Agent 的角色清单中加入：

```yaml title="Agent file — subagents fragment"
subagents:
  - markdown: subagent-builtin-explorer
```

对同一内置 ID 的显式引用会去重。如果另一个子级也叫 `explorer`，则会冲突：重命名自定义子级或移除内置选择。`subagent-builtin-*` ID 归包所有。包升级和配置编辑影响后续 Run；活动 Run 保留已经捕获的定义。

## 编写 Markdown subagent

辅助角色只需要不同指令、仍使用父级 Model 和 Capability 时，选择 Markdown。

1. 将以下完整文件保存到所选根 YAML 旁的 `subagents/investigator.md`：

   ```markdown title="subagents/investigator.md — complete file"
   ---
   name: investigator
   description: Trace a bounded repository question and report evidence.
   instruction: Use for focused read-only discovery before implementation.
   tools: [glob, grep, ls, view]
   ---

   Find the relevant implementation and tests. Return exact file paths and explain
   how the pieces connect. Do not modify files. Stop at the assigned scope.
   ```

2. 将其 ID 加入 `agents/coder.yaml`。保留列表中已有的子级：

   ```yaml title="agents/coder.yaml — subagents fragment"
   subagents:
     - markdown: subagent-investigator
   ```

3. 运行 `a13n-harness-ui config validate`，然后启动 `a13n-harness-ui --agent agent-coder`，让它将文件阅读任务委派给 **`investigator`**。用 `/subagents` 检查结果。

父级必须提供列表中的工具。允许列表包含未知名称时，组合会失败。此例公开只读文件工具；强制 Harness 基础设施会单独保留。

| Frontmatter 字段 | 必需 | 用途                                          |
| ---------------- | ---- | --------------------------------------------- |
| `name`           | 是   | 委派名称；也用于派生默认 ID `subagent-<name>` |
| `description`    | 是   | 展示给父级的简短角色说明                      |
| `id`             | 否   | 显式稳定的 `subagent-` 资源 ID                |
| `instruction`    | 否   | 展示给父级的额外路由指导                      |
| `tools`          | 否   | 精确可见工具过滤器，可用列表或逗号分隔名称    |

正文提供子级指令。省略 `tools` 时，子级继承父级的可见工具过滤器。Markdown 子级没有嵌套角色清单，始终继承父级 Model。移除旧定义中的 `model` 字段，包括 `model: inherit`；独立设置应使用 Agent 引用。

## 引用已有 Agent 作为 subagent

辅助角色需要自己的 Model、Capability、MCP 选择、工具分组或子级时，使用 Agent 引用。以下步骤接续上面的 `agent-coder` 示例。

### 1. 定义审查 Model

将以下完整文件保存为 `models/review.yaml`，或复用已有 Model ID：

```yaml title="models/review.yaml — complete file"
schema_version: "1"
kind: model
id: model-review
name: Review API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
settings:
  thinking: low
```

此例复用父级凭据，但采用不同推理设置。请选择提供方和模型支持的设置。

### 2. 定义子 Agent

将以下完整文件保存为 `agents/reviewer.yaml`：

```yaml title="agents/reviewer.yaml — complete file"
schema_version: "1"
kind: agent
id: agent-reviewer
name: Independent reviewer
model: model-review
instructions: |
  Review the assigned change. Report concrete material defects with file paths.
  Return findings to the parent without editing files.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: false
tools: [glob, grep, ls, view]
harness_plugins: []
mcp_servers: []
subagents: []
```

工具允许列表将此 Agent 限定为只读文件工具，加上强制 Harness 基础设施。空 Plugin 和 MCP 列表表示不选择这两类来源。

### 3. 在父级清单中添加引用

将以下条目加入 `agents/coder.yaml`，保留已有子级：

```yaml title="agents/coder.yaml — subagents fragment"
subagents:
  - agent: agent-reviewer
```

运行 `a13n-harness-ui config validate`，然后启动新的 `agent-coder` 对话。让父级将一次聚焦审查委派给 **`agent-reviewer`**。该子级使用 `model-review`。也可用 `a13n-harness-ui --agent agent-reviewer` 直接启动它。

一个角色清单可同时包含 Agent 和 Markdown 条目。直接子级的委派名称必须唯一，Agent 引用不能形成循环。委派遵循应用已有的 Project、Environment 和授权规则。

## 名称与引用速查

| 值              | 示例                        | 使用位置                                          |
| --------------- | --------------------------- | ------------------------------------------------- |
| 文件名          | `agents/reviewer.yaml`      | 文件整理；可与 ID 不同                            |
| Agent `id`      | `agent-reviewer`            | `--agent`、`defaults.agent`、`- agent:`、委派名称 |
| Agent `name`    | `Independent reviewer`      | 面向用户的标签                                    |
| Markdown `id`   | `subagent-investigator`     | `- markdown:` 引用                                |
| Markdown `name` | `investigator`              | 委派名称                                          |
| 内置名称        | `explorer`                  | 根级 `subagents.include` 和委派名称               |
| 内置来源 ID     | `subagent-builtin-explorer` | 显式 `- markdown:` 引用                           |

## 工具搜索分组

Agent 有大量工具时，可使用分组。模型通过固定代理发现和调用组内工具，无须预先接收每个工具的 schema。

1. 先配置并选择来源。以下示例假设已有且启用了 MCP 资源 `mcp-docs`；参见 [MCP 服务器](mcp.md)。

2. 将以下片段合并到 **Agent YAML**：

   ```yaml title="agents/coder.yaml — merge fragment"
   tool_search:
     groups:
       knowledge:
         description: Search project documents
         mcp_servers: [mcp-docs]
   ```

3. 运行 `a13n-harness-ui config validate` 和 `a13n-harness-ui config show --format json`，检查 `tool_search_previews`。这些命令只检查方案，不连接 MCP。

4. 在新对话中提出该分组能处理的任务。模型会使用 `discover_tools`、`describe_tool` 和 `call_tool`。`list_tools(detail="full")` 配合 `next_offset` 可分页列出目录。

分组也可通过资源 ID 选择 `harness_plugins`，通过已启用 Capability 的键选择 `capabilities`。一个来源只能属于一组。关闭的来源仍保持关闭；未分组的已启用来源仍直接展示。没有准备好工具的组不公开控件。Content Plugin 提供 Skill 和 subagent，不是工具来源。

| 设置                      | 规则                                                                |
| ------------------------- | ------------------------------------------------------------------- |
| 组名                      | 以字母开头；最多 32 个字母、数字、下划线或连字符；不能包含 `__`     |
| `description`             | 非空，最多 512 字符                                                 |
| 成员                      | 已存在的精确来源 ID 或 Capability 键；组内工具名不同                |
| 可选 `tool_search.config` | 代理控件名称和结果限制；`mode` 省略或设为 `proxy`                   |
| Agent `tools` 允许列表    | 填写 `knowledge__lookup` 等规范目标名，不能只填写代理的 `call_tool` |

重命名分组会改变规范目标名，需更新相应允许列表。分组保留工具执行权限和 CodeAct 适用条件。发现操作不会向后续请求添加成员 schema。独立 Agent 子级使用自己的分组；Markdown 子级继承父级方案。分组需在 YAML 中编辑，WebUI 尚不支持。变更应用于后续 Run。

代理控件和执行细节参见 [ToolSearch](../a13n-harness/tool-search.md)。旧配置应将 `mode: native` 改为 `proxy`，并移除 `strategy`。

## Agent 文件参考

每个文件需要 `schema_version: "1"`、`kind: agent`、唯一的 `agent-` ID 和显示名称 `name`。

| 字段              | 默认值 | 含义                                                    |
| ----------------- | ------ | ------------------------------------------------------- |
| `model`           | `null` | Model ID；运行该 Agent 前必须配置                       |
| `instructions`    | `""`   | 在包内系统提示基础上添加的指令                          |
| `capabilities`    | `[]`   | 来自已安装目录的有序 `{capability, configuration}` 条目 |
| `harness_plugins` | `null` | 继承根默认值；`[]` 不选；列表选择精确 ID                |
| `mcp_servers`     | `null` | 继承根默认值；`[]` 不选；列表选择精确 ID                |
| `tools`           | `null` | 默认集成工具面；`[]` 移除可选工具；列表为精确允许列表   |
| `tool_search`     | `null` | 工具分组和可选代理配置                                  |
| `subagents`       | `[]`   | 有序 Agent 或 Markdown 引用                             |

Plugin 和 MCP 默认值初始化新 Thread 的选择。已有 Thread 保留已保存的列表，直到显式修改。资源编辑会在后续 Run 解析；活动和已保存执行保留捕获的配置。

Capability 设置应放在该 Capability 的 `configuration` 中，不放在 Agent 顶层。即使 Agent 选择了相应 Capability，根级工具关闭开关仍然生效。

## 指令与指导

将可复用角色指令放在 Agent `instructions` 或 Markdown 子级正文中。将仓库约定放在工作目录的 `AGENTS.md` 中。将跨项目指导放在根 YAML 旁的 `AGENTS.md` 中。

这些内容与包内基础系统提示共同生效。每个 Run 捕获自己的指令。指导提供上下文，不授予执行权限。

Harness UI 通过 Environment 读取工作目录的 `AGENTS.md`。它还会列出直属子目录和附近 `.agents/skills` 目录中的指导入口，供 Agent 按需检查。这种有界发现不会加载子目录正文，也不会改变所选 Skill 目录；它不扫描祖先目录，不回退到 `RULES.md` 或 `AGENTS.override.md`。全局和工作目录指导保留在原生历史中，不在普通 TUI 历史中显示。

要关闭项目入口提示，显式添加 `file_context` Capability，设置 `configuration: {include_project_guidance: false}`，并保留其他 Capability 条目。

## 导入外部定义

使用 TUI 的 `/import` 可进行引导式导入。选择 Codex 或 Claude Code，选择来源范围，检查预览，再确认**导入并启用**。导入的辅助角色继承父级 Model 和工具。

独立转换流程：

1. 预览所选产品和范围：

   ```console
   a13n-harness-ui import subagents --product claude-code --scope user
   ```

2. 阅读目标路径和诊断。确认预览正确后，应用同一选择：

   ```console
   a13n-harness-ui import subagents --product claude-code --scope user --apply
   ```

3. 将导入的各个 `subagent-` ID 加入 Agent 的 Markdown 角色清单，再按前文验证并测试委派。独立导入只写入定义，不自动加入角色清单。

CLI 也接受 `--product codex` 或 `--product cursor`。导入仓库定义时，使用 `--scope project --project-root /absolute/path/to/repository`。

独立导入会保留可表示的工具名，并诊断不支持的 Model、权限、钩子和 MCP 设置。`--apply` 将预览定义写入本地配置，可能替换已有目标，但不会修改来源产品的文件。导入是一次性转换。引导式导入分别报告定义保存和角色清单更新结果；部分成功时，可据此判断还需完成哪一步。
