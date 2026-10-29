---
title: 工具与扩展类型
description: 为 Agent 添加工具，检查工具是否生效，并选择合适的集成类型。
---

为 Agent 添加新工具时，先确定由谁提供。多数内置功能是通过 Agent YAML 选择的 **Capability**。外部服务器使用 **MCP**。Environment 决定文件和 shell 操作在哪里运行。

| 我需要……                        | 配置……                           | 参考……                                                   |
| ------------------------------- | -------------------------------- | -------------------------------------------------------- |
| 读取文件或运行命令              | Agent 上的 `dynamic_environment` | [文件与 shell](#files-and-shell)                         |
| 通过 Model 提供方搜索或生成图像 | 原生工具 Capability              | [原生工具](native-and-web-tools.md)                      |
| 连接外部工具服务器              | MCP 资源及其选择                 | [MCP 设置](mcp.md)                                       |
| 添加可复用的书面指导            | Skill 或 Markdown subagent       | [Skill 与 Content Plugin](skills-and-content-plugins.md) |
| 改变执行机器或隔离方式          | Environment 配置                 | [Environment](environments-and-projects.md)              |
| 添加已安装的 Python 中间件      | Harness Plugin 资源              | [插件文件](#harness-plugin-and-run-extension-files)      |

## Agent Capability

使用已有 [Agent](agents-and-subagents.md#create-an-agent-from-files)。运行 `a13n-harness-ui config path` 找到配置树。在 `configuration_path` 所示根 YAML 旁的 `agents/` 中编辑对应文件。

1. 将所需条目加入 Agent 的 `capabilities` 列表。保留无关条目；已有同名 Capability 时编辑原条目，不要重复添加。
2. 将该 Capability 的设置放在它自己的 `configuration` 映射下。
3. 运行 `a13n-harness-ui config validate`。除 `valid` 外，也检查 `capability_warnings`。不可用或配置无效的普通 Capability 可能被跳过，而其余配置仍可使用。
4. 用该 Agent 启动新对话，让它使用该功能，并检查实际工具结果。配置通过检查并不等于连接可用或 Model 支持该工具。

已安装的 Capability 定义自己的设置 schema。YAML 通过目录键选择实现，不负责安装 Python 代码或导入任意模块。普通选择器不列出权限和审查 Capability；shell 审查应使用下方根级快捷配置。已有的显式权限策略仍受支持。

### 文件与 shell

将以下片段合并到 Agent 文件：

```yaml title="Agent file — capabilities fragment"
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
```

选择支持所需文件或 shell 操作的 [Environment](environments-and-projects.md)。然后先让 Agent 列出工作目录，再要求修改。工具同时需要 Capability 和支持相应操作的 Environment；根级工具开关可以全局关闭它们。

Agent 的 `tools` 列表进一步按精确名称过滤可见工具。要只公开 `glob`、`grep`、`ls` 和 `view`，参见[只读辅助角色示例](agents-and-subagents.md#write-a-markdown-subagent)。

### Shell 审查

要审查 shell 请求，将以下片段合并到**根** `a13n-harness-ui.yaml`。`model-review` 必须是已有 Model 的 ID：

```yaml title="a13n-harness-ui.yaml — merge fragment"
security:
  shell_review:
    enable: true
    model: model-review
    risk_threshold: high
```

验证后，在新 Run 中测试策略。根级审查字段覆盖对应的 shell 审查设置，并保留无关的 Agent 规则。`enable: false` 保持显式编写的 Agent 策略不变。审查负责评估请求，Environment 负责隔离。批准行为、错误策略和 Guardian 关联见 [shell 审查用法](configuration-recipes.md#configure-shell-review)。

### 上下文管理

Agent 需要监测上下文、生成交接摘要和压缩长历史时，添加这些条目：

```yaml title="Agent file — capabilities fragment"
capabilities:
  - capability: runtime_context
    configuration: {}
  - capability: handoff
    configuration: {}
  - capability: compaction
    configuration: {}
```

在 Model 的 `model_characteristics` 中统一设置上下文预算，参见 [Model 预算](models-and-authentication.md)。这些 Capability 从预算派生常规默认值。需要单独覆盖时，在相应 Capability 的 `configuration` 中配置 `runtime_context.context_window_tokens`、`handoff.summary_reminder_tokens` 或 `compaction.trigger_tokens`。显式值优先。

### 重复动作循环

循环检测需要显式启用。在 Agent 文件中添加原生 Capability：

```yaml title="Agent file — capabilities fragment"
capabilities:
  - capability: DetectLoops
    configuration:
      action: nudge
```

`nudge` 在出现重复调用、相同结果、交替调用或重复文本时提醒 Model 改变方法。使用 `action: error` 可在检测到循环时让 Run 失败。正常轮询任务应提高阈值/窗口，或设置 `repeated_call_threshold: null` 和 `repeated_result_threshold: null`。检测器只观察 Model 层的调用，CodeAct 内部的 host 调用不在检测范围内。每个原生 Model attempt（包括继续会话和恢复重试）都会重新计数。硬预算仍由用量限制控制。默认值与 Python 组合方式见 [Harness 循环检测](../a13n-harness/capabilities.md#detect-repeated-action-loops)。

### 任务、提问与 CodeAct

Harness UI 默认添加 `file_context` 和 `working_state`。根级开关启用时，还会添加 `user_interaction` 和 `codeact`。Skill、Web 访问、交接和压缩等其他功能需要在 Agent 中选择。

| 功能       | 配置                                                                        | 检查方法                                                        |
| ---------- | --------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 任务和笔记 | 默认 `working_state`；显式 `configuration: {notes_enabled: false}` 关闭笔记 | 让 Agent 创建任务清单；F2 显示已保存的任务事实                  |
| 结构化提问 | 根级 `tools.enable_ask_user_question`                                       | 问题可以暂停并等待你回答                                        |
| CodeAct    | 根级 `tools.enable_codeact`                                                 | Agent 可通过 `run_code` 或 `run_program` 编排符合条件的工具调用 |

提问和 CodeAct 开关也控制显式 Agent 选择。CodeAct 执行受限 Python，通过工具完成副作用，并遵守工具已有权限。`store`、`load` 和 `forget` 管理显式 JSON 状态；上下文显示已存键名。需要改变状态限制时，在 `codeact` Capability 下配置 `max_state_bytes` 和 `max_state_entries`。参见[根级工具设置](configuration.md#built-in-tools-and-subagents)和[问题处理](everyday-use.md#approvals-and-questions)。

## 原生搜索与图像生成

工具由 Model 提供方提供时，使用[原生搜索与图像生成用法](native-and-web-tools.md#native-search-and-image-generation)。这些 Agent 选择独立于 Host 侧 Web 搜索和抓取提供方。

## MCP 服务器

按照 [MCP 设置](mcp.md)创建服务器资源、选择其 ID，再测试一次工具调用。创建文件与选择服务器是两个步骤。

### MCP 字段参考

命令、HTTP、身份验证和连接生命周期设置见 [MCP 字段参考](mcp.md#mcp-field-reference)。

## Skill

通过 [Skill 与 Content Plugin](skills-and-content-plugins.md)添加可复用流程或参考资料。选择 `skills` Capability，并通过 Environment 提供来源。Skill 发现为 Agent 提供可阅读材料；执行仍使用已配置的工具和权限。

## Content Plugin

Content Plugin 打包可编辑的 Skill 和 Markdown subagent。[安装内容包](skills-and-content-plugins.md#install-a-content-plugin)后，选择所需内容，并在新 Run 中测试。[卸载](skills-and-content-plugins.md#remove-a-content-plugin)会删除安装目录及其中的本地编辑。分发自己的内容包时，使用[仓库格式](skills-and-content-plugins.md#author-a-content-plugin-repository)。

## Harness Plugin 与 Run Extension 文件

这些资源选择已安装的 Python 集成。编写文件前，先将集成安装到 Harness UI 所在的 Python 环境，并从集成文档获取工厂键和配置 schema。

1. 在所选根 YAML 旁的 `extensions/` 下创建资源文件。
2. 在对应 Agent、Project 或根默认值中选择资源 ID。
3. 运行 `a13n-harness-ui config validate`，修复缺失工厂或无效配置错误。
4. 用该选择启动新 Thread，测试集成文档描述的行为。

以下是**模板**，不是内置记忆插件。请将 `vendor.memory` 及其配置替换为已安装集成提供的值：

```yaml title="extensions/memory.yaml — template; replace factory key"
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory integration
plugin_key: vendor.memory
configuration: {}
```

对此资源，将 `harness_plugins: [plugin-memory]` 合并到 Agent，并保留其他已选 ID。已有 Thread 保留已保存的 Plugin 选择；修改创建默认值不会改写它们。

| 资源 `kind`                 | ID 前缀        | 必需实现键                    | 设置                                                           | 选择位置                                                            |
| --------------------------- | -------------- | ----------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------- |
| `harness_plugin`            | `plugin-`      | `plugin_key`                  | `configuration`，默认 `{}`                                     | Agent 或根级 `defaults.harness_plugins`                             |
| `environment_run_extension` | `extension-`   | `extension_key`               | `configuration`，默认 `{}`                                     | 新 Thread 使用 Project 或根级 `defaults.environment_run_extensions` |
| `environment_profile`       | `environment-` | `provider_key`、`adapter_key` | `provider_configuration`、`adapter_configuration`，各默认 `{}` | Environment 选择                                                    |

三类资源也都需要 `schema_version: "1"`、`kind`、`id` 和 `name`。使用集成提供的凭据引用字段，不要将密钥存入 YAML。Provider 配置流程见[自定义 Environment 配置](environments-and-projects.md#custom-environment-profiles)。
