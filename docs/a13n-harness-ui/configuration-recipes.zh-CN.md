---
title: 常见配置用法
sidebarTitle: 常见用法
description: Model、Agent、工具、MCP 服务器、Skill 和审查策略的常见配置修改。
---

先完成[设置](setup.md)。如果要手动编辑，先找到选中的目录，再验证修改：

```console
a13n-harness-ui config path
a13n-harness-ui config validate
```

将各代码片段合并到该目录（通常为 `~/.a13n-harness-ui/`）下的指定文件。使用自定义配置树时，在子命令前加上 `--config /path/to/a13n-harness-ui.yaml`。验证检查字段和引用，不连接提供方或 MCP 服务器。检查警告：未知新增字段会保留，但不会使用。Project 目录在 Run 开始前必须可用，验证时不要求可用。

## 修改默认 Agent

```yaml title="a13n-harness-ui.yaml"
defaults:
  agent: agent-coder
```

`agent-coder` 必须是现有 Agent 的 `id`，不是文件名或显示名称。这会初始化新对话；已有对话保留所选 Agent。使用 `/agent agent-coder` 修改已有对话。

要在不编辑文件的情况下创建其他 Agent，运行 `a13n-harness-ui add agent`。

## 修改 Model、推理或上下文预算

模型连接和请求设置属于 **`models/<name>.yaml`**，不应放在根 YAML 或 Agent 的 `capabilities` 中。

以下是完整的 API 密钥 Model 示例。选择端点支持的路由：

```yaml
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
settings:
  thinking: high
  openai_service_tier: default
model_characteristics:
  capabilities: [image_understanding]
  context_window_tokens: 128000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

- `route` 选择提供方和模型，不会创建凭据。
- `authentication.env` 指定 Harness UI 启动时可用的已导出变量。使用本地保存的密钥时，先运行 `a13n-harness-ui auth key set key-primary`，再将 `env` 替换为 `credential_ref: key-primary`。
- `thinking` 请求支持的推理强度，与 TUI 简洁/详细显示无关。
- `context_window_tokens` 是本地工作预算，不会提高提供方上限。这里的提醒阈值为 83,200 token，压缩阈值为 115,200 token。
- `capabilities` 声明原生输入支持；开启模态前，应验证实际端点。

在 **`agents/<name>.yaml`** 中选择：

```yaml
model: model-primary
```

检查永久修改时，用 `/model default` 清除 Project 记住的 Model，并用 `/thinking default`、`/fast reset` 和 `/pro reset` 移除临时请求覆盖。已接受的 Model 修改影响后续 Run；活动 Run 保留捕获的设置。

所有字段和原生设置行为见 [Model 参考](models-and-authentication.md#model-file-reference)。

## 连接 OpenAI 兼容端点

```yaml title="models/compatible.yaml"
schema_version: "1"
kind: model
id: model-compatible
name: Compatible API
route: openai-chat:your-model-id
authentication:
  kind: api_key
  env: COMPATIBLE_API_KEY
model_configuration:
  base_url: https://api.example.com/v1
settings: {}
```

替换模型 ID 和 URL。使用端点实现的协议；OpenAI 兼容 Chat Completions 与 Responses 不同。支持本地 HTTP 端点。URL 不得含凭据、查询参数或片段。

`model_configuration.base_url` 配置连接；`settings` 配置请求。订阅路由和原生 `xai:` SDK 路由不接受此 HTTP 覆盖。要保留提供方专用推理行为，应优先使用 `deepseek:`、`zai:` 或 `moonshotai:` 等支持的原生路由，而不是通用兼容连接。

## 添加编程指令

为单个 Agent 编辑 **`agents/<name>.yaml`**：

```yaml
instructions: |
  Read the relevant code before editing.
  Keep changes focused and validate the affected behavior.
  Report changed files, checks, and remaining limitations.
```

为该配置树的所有 Agent 提供指导，编辑**根 YAML 旁的 `AGENTS.md`**。为单个 Project 或目录提供指导，编辑**其工作目录中的 `AGENTS.md`** 。不会扫描祖先目录的指导文件。这些文件提供指引，不授予或移除执行权限。

## 开启指定内置 subagent

```yaml title="a13n-harness-ui.yaml"
subagents:
  include: [explorer, code-reviewer]
```

可用名称为 `explorer`、`code-reviewer` 和 `executor`。`[]` 关闭自动加入。无须复制其 Markdown 文件。内置角色继承父级 Model，加入根角色清单，不会递归加入每个子级。

需要独立 Model 的子级，应创建 Agent 资源，并在父 Agent 的 `subagents` 中添加 `- agent: agent-reviewer`。只有指令的角色使用 [Markdown subagent](agents-and-subagents.md#write-a-markdown-subagent)。

## 配置 shell 审查

```yaml title="a13n-harness-ui.yaml"
security:
  shell_review:
    enable: true
    risk_threshold: extra_high
    model: model-review
    on_flagged: approval_required
    on_error: allow
```

1. 将 `model-review` 创建为 Model 资源，或选择已有 Model ID。审查器不是 `code-reviewer` subagent，不能执行工具。
2. 将上面的映射加入根 YAML，运行 `a13n-harness-ui config validate`。
3. 开始新 Run。快捷配置审查 Agent 及其子级的 `environment.shell_exec`，不为其他工具启用审查。

Codex 和 Grok 设置会创建独立审查 Model。ChatGPT、GitHub Copilot 和 API 密钥设置复用所连接的 Model。每次审查都发出模型请求，按该连接计算用量和费用。可选的 Codex Guardian 关联见[根配置参考](configuration.md#shell-review-shortcut)。

| 设置             | 行为                                                                |
| ---------------- | ------------------------------------------------------------------- |
| `risk_threshold` | `low`、`medium`、`high` 或 `extra_high`；达到或超过阈值的调用被标记 |
| `on_flagged`     | `deny` 或 `approval_required`                                       |
| `on_error`       | 非超时错误使用 `deny`、`approval_required` 或 `allow`               |
| 审查器超时       | 始终拒绝执行                                                        |
| 人工审批超时     | 独立的 `tools.interaction_timeout_seconds`，默认 120 秒             |

快捷配置合并到 Agent 的 `ToolPermissionsCapability`。其 shell `review` 规则会替换显式 `allow`、`deny` 或 `ask`；提供的审查字段替换对应 Agent 字段。无关规则和审查指令保留。省略/null 字段继承 Agent 策略，再回退到 `extra_high`、实际 Agent Model、标记调用的 `approval_required` 和非超时错误的 `allow`。`allow` 仍执行剩余权限检查。

设置 `enable: false` 停止注入快捷配置；显式 Agent 策略保留。高级策略使用带嵌套 `review` 的单个 `ToolPermissionsCapability`，只审查权限为 `review` 的工具。仅有审查器或风险规则不会开启审查。

用 `/review request-id` 查看可用的风险和原因详情。审查历史是证据，不授予权限。审查不隔离文件或网络。缺少 Model 或合并策略无效时，验证失败。已接受修改影响后续 Run。

### 使用 TypeSafe Jev 审查

Jev 是普通 API 密钥 Model，不是 subagent 或独立审查服务。创建 `models/jev-review.yaml`：

```yaml title="models/jev-review.yaml"
schema_version: "1"
kind: model
id: model-jev-review
name: Jev tool review
route: typesafe:jev-latest
authentication:
  kind: api_key
  env: TYPESAFE_API_KEY
```

在根文档中设置 `security.shell_review.model: model-jev-review`，启动 Harness UI 前导出 `TYPESAFE_API_KEY`。对话 Agent 仍应使用支持文本的模型。为可复现评估，可将 `jev-latest` 换成经过测试的带版本 Jev ID。

要使用 TypeSafe 兼容网关而非默认 `https://api.typesafe.ai`，在 Model 文档中添加以下内容（端点必须实现原生 TypeSafe 协议，不能使用 OpenAI Chat Completions）：

```yaml
model_configuration:
  base_url: https://jev-gateway.example
```

Jev 的严重程度评分为 0 = `low`、1 = `medium`、2 = `high`、3 = `extra_high`；置信度不改变决策。Jev 不返回文本解释，Harness UI 也不向第二个 Model 请求解释。常规阈值、权限、超时和错误策略仍适用。替换现有审查器前，评估具有代表性及对抗性的命令。

## 开启 MCP 服务器

```yaml title="mcp/docs.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation server
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization: "Bearer ${DOCS_MCP_TOKEN}"
```

替换端点，并在启动 Harness UI 的进程中导出其 token。然后在 **`agents/<name>.yaml`** 中选择 ID：

```yaml
mcp_servers: [mcp-docs]
```

创建服务器文件只注册资源；选择才启用。初始化对话选择时，`mcp_servers: null` 继承默认值，`[]` 不选任何项。已有对话保留 MCP 选择，因此测试修改后的默认值时需使用新对话。

命令传输、客户端式 JSON、凭据引用和捕获行为见 [MCP 配置](mcp.md)。

## 开启 Skill

**文件：`agents/<name>.yaml`，在 `capabilities` 中添加条目**

```yaml
capabilities:
  - capability: skills
    configuration: {}
```

保留其他条目。自动来源包括所选 Environment 中的 Project `.agents/skills`、用户 `~/.agents/skills`、已安装 Content Plugin 和发行版配置 Skill。见[来源优先级与离线指引](skills-and-content-plugins.md#automatic-sources-and-precedence)。Skill 目录包含 `SKILL.md`。在 TUI 中输入 `$` 可查找可用 Skill。

可选 `configuration.roots` 添加显式绝对 **Environment 路径**，不是任意宿主机路径。仅列出宿主机目录，不会让 Sandbox 获得访问权限。见 [Skill 来源](skills-and-content-plugins.md)。

## 修改显示与工具开关

```yaml title="a13n-harness-ui.yaml"
display:
  theme: dark
  mode: detailed
  show_status: true
  max_tool_result_lines: 15
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 300
  enable_codeact: true
```

显示默认值在启动时生效；`/theme` 和 `/mode` 修改实时展示，不改变模型推理、权限或已保存模型上下文。

交互超时控制 TUI 问题、审批和外部结果，或完整 [WebUI 决策批次](webui.md#questions-and-approval-timeouts)，不限制模型执行。到期绝不授予审批。设置 `enable_codeact: false` 从新解析的 Run 中移除内置 CodeAct runner 和状态工具。全局关闭开关也优先于显式 Agent Capability 选择。

允许范围见[根字段](configuration.md#display-settings)。

## 使用多个目录

创建带有序绝对根目录的 [Project 资源](environments-and-projects.md#project-file-reference)。从其**第一个** 根目录启动 TUI 以选择该 Project。单目录用户不需要 Project 文件：TUI 使用启动目录。

使用 `/environment` 改变执行模式。Project 根目录组织工作，不限制 Full Control 的宿主机权限。

## 修改为什么没有生效？

1. 检查 `config path`：你是否在编辑该进程选中的配置树？
2. 运行 `config validate`：修复无效字段和缺失引用，检查 Capability 警告。
3. 用 `/status` 检查所选 Agent 和实际 Model。
4. 如果设置了临时覆盖，将其移除。
5. 等待**新 Run**：配置绝不改变正在执行的捕获。
6. 如果修改了全局资源默认值，开启新对话。
7. 如果修改了进程或显示设置，重启 Harness UI。

磁盘上的无效修改可能使上一版已接受配置继续生效。不要删除数据目录来强制应用修改：它也存放已保存对话和凭据。见[配置优先级](configuration.md#what-wins-and-when-edits-apply)。
