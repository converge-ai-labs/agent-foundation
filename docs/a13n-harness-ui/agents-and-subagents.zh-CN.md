---
title: 智能体与子智能体
description: 定义自定义 YAML 智能体与轻量级 Markdown 子智能体角色，定制提示词、继承或独立模型以及精细的工具权限。
---

**Agent**（`agents/*.yaml`）将模型、指令、能力与可选子智能体组合在一起。建议先创建一个根智能体并跑通，再按需添加内置、动态或自定义子智能体来委派专项任务。

## 通过文件创建智能体

运行 `a13n-harness-ui config path` 找到配置目录（通常为 `~/.a13n-harness-ui/`）。

1. **创建模型**（或直接复用[初始化向导](setup.md)已生成的模型 ID）：

```yaml title="models/primary.yaml"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

2. **在 `agents/coder.yaml` 中创建根智能体：**

```yaml title="agents/coder.yaml"
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

3. **校验并启动：**

```console
a13n-harness-ui config validate
cd /absolute/path/to/your-repository
a13n-harness-ui --agent agent-coder
```

如需设为默认智能体，在 `a13n-harness-ui.yaml` 中设置 `defaults: {agent: agent-coder}`。

## 内置子智能体

Harness UI 内置三个辅助角色，在**根配置文件**（`a13n-harness-ui.yaml`）中按需启用：

| 名称            | 职责                           |
| --------------- | ------------------------------ |
| `explorer`      | 定位代码实现并返回精确文件引用 |
| `code-reviewer` | 审查变更并报告实质性缺陷       |
| `executor`      | 完成边界清晰的具体子任务       |

```yaml title="a13n-harness-ui.yaml"
subagents:
  include: [explorer, code-reviewer]
```

内置子智能体继承父智能体的模型、能力与可见工具，可通过 `a13n-harness-ui config subagents` 查看目录。若只想为单个智能体挂载某个内置角色，可在该智能体的 `subagents` 列表中添加 `- markdown: subagent-builtin-explorer`。

## 动态子智能体与结果回传

启用 `spawn_subagent` 后，根对话可以按需创建使用当前对话模型的叶子子智能体：

```yaml title="a13n-harness-ui.yaml"
subagents:
  dynamic:
    enabled: true
```

- 即使 `agent_creation.allowed_models` 为空，预先配置的子智能体和动态子智能体也可以使用父智能体当前运行所用的模型。要允许其他模型，将其 ID 添加到 `allowed_models`。如果希望 `spawn_subagent` 未指定 `model` 时使用其他模型，将 `agent_creation.default_model` 设为列表中的一个 ID。
- 动态子智能体继承父级工具与环境，以全新历史启动，完成后自动回传结果。
- 主智能体可通过 `subagent_info`、`wait_subagent`、`steer_subagent` 与 `resume_subagent` 管理运行中的子任务。

## 编写 Markdown 子智能体

若子智能体只需不同指令或更窄的工具白名单、且复用父级模型与能力，在 `subagents/` 下创建 Markdown 文件即可：

```markdown title="subagents/investigator.md"
---
name: investigator
description: Trace a bounded repository question and report evidence.
instruction: Use for focused read-only discovery before implementation.
tools: [glob, grep, ls, view]
---

Find the relevant implementation and tests. Return exact file paths and explain
how the pieces connect. Do not modify files.
```

在 `agents/coder.yaml` 中引用：

```yaml
subagents:
  - markdown: subagent-investigator
```

## 将已有智能体引用为子智能体

当子智能体需要独立的模型、能力或 MCP 服务器时，将其定义为普通 `agents/*.yaml` 文件并按 ID 引用（名称与指令由该智能体文件本身提供）：

```yaml title="agents/coder.yaml"
subagents:
  - agent: agent-specialist
```

## 指令与引导文件

- **`instructions`：** 在智能体 YAML 中定义。
- **全局 `AGENTS.md`：** 放在 Harness UI 配置根目录，自动注入根对话与子对话。
- **项目 `AGENTS.md`：** 从当前工作区根目录自动发现。

## 工具搜索分组

当智能体接入大量工具（如大型 MCP 服务器）时，可在智能体上配置 `tool_search` 分组，按需延迟加载工具 Schema，在不改变权限的前提下显著降低提示词 Token 消耗。

## 智能体文件参考

| 字段                             | 默认值 | 用途                                                        |
| -------------------------------- | ------ | ----------------------------------------------------------- |
| `id`、`name`                     | 必填   | 唯一 `agent-` 前缀 ID 与显示名称                            |
| `model`                          | 必填   | 已保存的模型 ID                                             |
| `instructions`                   | `""`   | 智能体角色提示词                                            |
| `capabilities`                   | `[]`   | 启用的能力列表（`dynamic_environment`、`web`、`skills` 等） |
| `tools`                          | `null` | 可选的精确工具名白名单                                      |
| `subagents`                      | `[]`   | `markdown:` 或 `agent:` 子条目列表                          |
| `mcp_servers`、`harness_plugins` | `null` | 选用的 MCP 服务器 ID（`null` 继承默认）与插件 ID 列表       |
| `tool_search`                    | `null` | 可选的分组工具按需发现计划                                  |
