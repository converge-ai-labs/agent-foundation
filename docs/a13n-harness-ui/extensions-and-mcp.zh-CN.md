---
title: 工具与扩展类型
description: 选择 Capability、Harness Plugin、MCP 服务器、Skill 或 Content Plugin。
---

按集成提供的功能选择机制，再在配置中选择已安装的键或资源 ID。YAML 可以选择可执行集成，但不会安装其 Python 代码。

| 机制                      | 添加的内容                                                 | 配置方式                                                             |
| ------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------- |
| Capability                | Agent 的工具、指令、钩子、设置或生命周期行为               | Agent `capabilities`                                                 |
| Harness Plugin            | 已安装的 Harness 集成                                      | `extensions/*.yaml`，再由 Agent/默认 `harness_plugins` 选择          |
| Environment 配置          | Provider 和 Project adapter 配置                           | `extensions/*.yaml`，再选择 Environment                              |
| Environment Run Extension | 每次 Run 的 Environment 集成                               | `extensions/*.yaml`，再由 `defaults.environment_run_extensions` 选择 |
| MCP 服务器                | 命令或远程服务器提供的外部工具                             | `mcp/*.yaml` 或 `mcp/*.json`，再由 Agent/默认 `mcp_servers` 选择     |
| Content Plugin            | 可编辑的 Skill 和 Markdown subagent 内容，不是 Python 扩展 | `a13n-harness-ui plugin` 命令                                        |

提供方原生工具和内置搜索/抓取见[原生工具与 Web 提供方](native-and-web-tools.md)。

## 原生搜索与图像生成

按照[原生搜索与图像生成用法](native-and-web-tools.md#native-search-and-image-generation)配置；这是 Agent 工具选择，与 Host Web 搜索不同。

## MCP 服务器

先[配置 MCP 服务器](mcp.md)，再在 Agent 或默认值中选择其 ID。

### MCP 字段参考

完整字段见 [MCP 字段参考](mcp.md#mcp-field-reference)。

## Agent 能力

每个 Agent 选择的形式是 `{capability: <catalog-key>, configuration: <JSON mapping>}`。Harness UI 提供内置能力以及可配置的已安装/原生能力。资源文件没有任意模块导入字段。

常见内置键包括 `dynamic_environment`、`documents`、`web`、`skills`、`working_state`、`user_interaction`、`runtime_context`、`handoff`、`compaction` 和 `codeact`。普通目录选项有意不包含权限和审查 Capability；请使用根级 `security.shell_review`。已有原始 Agent 选择仍然兼容。并非每个能力都自动开启。

完整的能力专用 schema 由已安装的 Harness/原生实现定义，不会全部展开到 Harness UI YAML 中。请查阅 [Harness 能力参考](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/a13n_harness/capabilities)及与你安装版本匹配的实现。`a13n-harness-ui config validate` 检查选中的键及其配置。

### 文件与 shell

```yaml
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
```

这会开启原生 Environment 工具，不会创建第二个本地 runner。在 Agent 上使用 `tools` 进一步精确过滤可见工具，使用 Environment 配置提供执行隔离。

### Shell 审查

在选中的根 `a13n-harness-ui.yaml` 中配置 shell 审查，不要通过 Agent 能力选择器配置：

```yaml
security:
  shell_review:
    enable: true
    model: model-review
    risk_threshold: high
```

`model` 是已配置的 **Model 资源 ID**，不是 subagent 引用或隐式提供方路由。此快捷配置将 shell 启动的权限和可选审查合并到一个 `ToolPermissionsCapability` 中；显式根字段优先，Agent 中无关的规则保留。`enable: false` 不会修改显式 Agent 策略。默认值、继承、错误和用量见[完整 shell 审查用法](configuration-recipes.md#configure-tool-review)。审查不提供文件系统或网络隔离，与 `code-reviewer` 子角色相互独立。

### 上下文管理

```yaml
capabilities:
  - capability: runtime_context
    configuration: {}
  - capability: handoff
    configuration: {}
  - capability: compaction
    configuration: {}
```

通常应在 Model 上配置 `model_characteristics`，使提醒和压缩默认值保持一致。高级显式设置包括 `runtime_context.configuration.context_window_tokens`、`handoff.configuration.summary_reminder_tokens` 和 `compaction.configuration.trigger_tokens`；显式值优先于派生值。

### 任务、提问与 CodeAct

默认通过原生工作状态提供任务和笔记工具，包括有上限的笔记上下文注入。`working_state` 接受 `notes_enabled: false` 等原生配置以关闭笔记。F2 显示已提交的任务事实，不是单独的 CLI 清单存储。

根级 `tools.enable_ask_user_question` 控制 `ask_user_question`；`tools.enable_codeact` 控制 CodeAct。两项开关也控制显式编写的能力选择。CodeAct 还公开 `store`、`load` 和 `forget`，用于随 Harness 续接保存显式 JSON 值；上下文中只投影已存键名。Agent 能力配置支持 `max_state_bytes` 和 `max_state_entries` 限制状态。其 `run_code` 和 `run_program` 执行受限 Python；宿主机效果通过符合条件的工具和现有策略实现，不提供无限制的 Python 文件系统或网络访问。默认值见[根配置参考](configuration.md#built-in-tools-and-subagents)，问题超时见[决策指南](everyday-use.md#approvals-and-questions)。

## Skill

显式和自动来源优先级、内置离线配置 Skill、文件访问和目录捕获见 [Skill 与 Content Plugin](skills-and-content-plugins.md)。选择 Skill 会增加知识发现能力，不会授予执行权限。

## Content Plugin

见 [Content Plugin 安装](skills-and-content-plugins.md#install-a-content-plugin)、[卸载删除行为](skills-and-content-plugins.md#remove-a-content-plugin)和[完整仓库格式](skills-and-content-plugins.md#author-a-content-plugin-repository)。它们是可编辑内容包，不是已安装的 Python Harness Plugin。

## Harness Plugin 与 Run Extension 文件

Harness Plugin 资源选择**已安装** 的工厂。请将以下示例键和配置替换为集成文档中的值：

```yaml
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory integration
plugin_key: vendor.memory
configuration: {}
```

保存到 `extensions/` 下，再在 Agent 或根默认值中选择 `harness_plugins: [plugin-memory]`。未知/未安装的键会导致验证失败；写入文件不等于安装。

| 资源类型                                       | 共享 `schema_version`、`kind`、`id`、`name` 之外的完整字段                                          |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `harness_plugin`（`plugin-` ID）               | 必需 `plugin_key`；`configuration` 默认 `{}`                                                        |
| `environment_run_extension`（`extension-` ID） | 必需 `extension_key`；`configuration` 默认 `{}`                                                     |
| `environment_profile`（`environment-` ID）     | 必需 `provider_key` 和 `adapter_key`；`provider_configuration` 和 `adapter_configuration` 默认 `{}` |

Run Extension 通过根级 `defaults.environment_run_extensions` 选择。Provider/adapter 和扩展专用配置属于已安装的实现，凭据应使用引用而非字面密钥字段。选择 provider 前，阅读[自定义 Environment 配置](environments-and-projects.md#custom-environment-profiles)。
