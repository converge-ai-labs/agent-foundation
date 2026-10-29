---
title: 常见配置用法
sidebarTitle: 常见用法
description: 修改 Model、Agent 或工具配置，并检查修改是否已被使用。
---

先让[第一个 Agent 正常工作](setup.md)，再使用这些配置用法。每个用法修改已有设置的一部分。交互式编辑可使用 WebUI 设置；下面的文件示例适合检查、共享和重现配置。

## 编辑前的准备

在 shell 中运行以下命令，找到所选配置：

```console
a13n-harness-ui config path
```

默认根文件为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`。下文的 `models/primary.yaml` 等路径相对于这个文件所在目录，**不是你的项目目录**。数据目录保存对话和凭据；项目目录保存 Agent 要处理的文件。

标为**完整文件**的示例可用来创建新资源。标为**合并到**的示例用于修改已有映射：保留其他字段和列表条目，不要重复添加相同的 YAML 键。引用使用资源的 `id`，与文件名或显示名称无关。

修改后运行：

```console
a13n-harness-ui config validate
```

先修复验证错误并检查警告，再测试功能。未知新增字段可能被保留，但不生效。验证检查配置，不检查提供方或 MCP 的连接。Project 目录必须在 Run 启动时可用。

使用自定义配置树时，验证和启动要使用相同的选择参数：

```console
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml
```

## 修改默认 Agent

1. 在 `agents/` 中找到目标 Agent 的 `id`，或用 `a13n-harness-ui add agent` 创建 Agent。

2. 将该 ID 合并到根文件：

   ```yaml title="a13n-harness-ui.yaml — 合并到 defaults"
   defaults:
     agent: agent-coder
   ```

3. 验证后开始新对话。在终端中用 `/status` 检查选中的 Agent。

默认值用于初始化新对话。要切换已有对话，使用 `/agent agent-coder`。

## 修改 Model、推理或上下文预算

这个示例创建 Model，将它分配给已有 Agent，再测试选择是否生效。如果只想调整当前 Model，直接编辑已有 Model 文件，不必创建新资源。

1. 通过 CLI 的不回显提示保存 API 密钥：

   ```console
   a13n-harness-ui auth key set key-primary
   ```

2. 创建下面的完整文件。必要时将路由替换为账户支持的模型：

   ```yaml title="models/primary.yaml — 完整文件"
   schema_version: "1"
   kind: model
   id: model-primary
   name: Primary API model
   route: openai-responses:gpt-5
   authentication:
     kind: api_key
     credential_ref: key-primary
   settings:
     thinking: high
   model_characteristics:
     context_window_tokens: 128000
     proactive_context_management_threshold: 0.65
     compact_threshold: 0.90
   ```

   `thinking` 请求受支持的推理级别。`context_window_tokens` 设置本地工作预算，不会提高提供方限制。这里的上下文提醒和压缩阈值分别来自预算的 65% 和 90%。请选择适合当前路由的预算和请求设置。

3. 打开正在使用的 Agent 文件，替换其 `model` 字段：

   ```yaml title="agents/coder.yaml — 合并到已有 Agent"
   model: model-primary
   ```

4. 验证配置。终端中，用 `/model default` 清除 Project 记住的 Model。如果曾设置临时请求覆盖，再用 `/thinking default`、`/fast reset` 和 `/pro reset` 清除。

5. 检查 `/status`，然后发送一条简短消息。下一次 Run 使用已接受的 Model 设置；正在执行的 Run 保留原设置。

通过环境管理凭据时，将 `credential_ref: key-primary` 替换为 `env: OPENAI_API_KEY`，并在启动 Harness UI 的进程中设置该变量。不要将密钥值写入 YAML。

Model 的连接字段、请求设置和本地上下文策略分别配置。添加提供方专用字段或媒体输入声明前，先查看 [Model 参考](models-and-authentication.md#model-file-reference)。

## 连接 OpenAI 兼容端点

准备好端点的 API base URL、模型 ID、支持的协议和 API 密钥。这个示例使用 Chat Completions；只有端点实现了 Responses 时，才选择 Responses 路由。

1. 用 `a13n-harness-ui auth key set key-compatible` 保存密钥。

2. 创建完整 Model 文件，替换 `your-model-id` 和示例 URL：

   ```yaml title="models/compatible.yaml — 完整文件"
   schema_version: "1"
   kind: model
   id: model-compatible
   name: Compatible API
   route: openai-chat:your-model-id
   authentication:
     kind: api_key
     credential_ref: key-compatible
   model_configuration:
     base_url: https://api.example.com/v1
   settings: {}
   ```

3. 将 Agent 的字段设为 `model: model-compatible`，验证后选择该 Agent。清除 Project Model 覆盖，再按上一个用法发送测试消息。

连接 URL 放在 `model_configuration.base_url`，请求参数放在 `settings`。支持本地 HTTP 端点；URL 不得包含凭据、查询参数或片段。原生 `xai:` SDK 和订阅路由不接受此 HTTP 覆盖。需要提供方专用推理行为时，使用受支持的原生路由，例如 `deepseek:`、`zai:` 或 `moonshotai:`，不要假定通用兼容路由会保留这些行为。

## 添加编程指令

只供一个 Agent 使用的指令，写入其已有的 `instructions` 字段：

```yaml title="agents/coder.yaml — 合并到已有 Agent"
instructions: |
  Read the relevant code before editing.
  Keep changes focused and validate the affected behavior.
  Report changed files, checks, and remaining limitations.
```

验证后选择该 Agent，开始下一次 Run。要让多个 Agent 共用指令，写入根 YAML 旁的 `AGENTS.md`。要描述代码库，写入工作目录中的 `AGENTS.md`。Harness UI 不扫描祖先目录寻找指导文件。

这些指令指导行为。实际文件和命令权限通过 [Environment 权限](environments-and-projects.md#execution-permissions)配置。

## 开启指定内置 subagent

1. 将需要的角色合并到根文件：

   ```yaml title="a13n-harness-ui.yaml — 合并到 subagents"
   subagents:
     include: [explorer, code-reviewer]
   ```

2. 验证后查看配置中的启用情况：

   ```console
   a13n-harness-ui config subagents --format json
   ```

3. 开始下一次 Run，使用更新后的角色清单。

可用角色是 `explorer`、`code-reviewer` 和 `executor`；`[]` 关闭自动加入。内置角色继承父级 Model，加入根角色清单，不会加入每个子级的清单。无须复制它们的 Markdown 文件。

子级需要自己的 Model 时，[引用 Agent](agents-and-subagents.md#reference-an-existing-agent-as-a-subagent)。只需要在父级 Model 上使用自定义指令时，[编写 Markdown subagent](agents-and-subagents.md#write-a-markdown-subagent)。

## 配置 shell 审查

Shell 审查让 Model 在权限决策前评估一次 shell 启动。它会产生额外模型请求，不隔离命令的文件系统或网络访问。

1. 配置审查 Model，或选择已有 Model ID。审查器是 Model 资源，不是 `code-reviewer` subagent。

2. 将以下策略合并到根 YAML，并把 `model-review` 替换为该 ID：

   ```yaml title="a13n-harness-ui.yaml — 合并到 security"
   security:
     shell_review:
       enable: true
       risk_threshold: extra_high
       model: model-review
       on_flagged: approval_required
       on_error: allow
   ```

3. 验证后开始新 Run。策略会审查 Agent 及其子级的 `environment.shell_exec`。产生审查请求后，用 `/review request-id` 查看可用的风险和原因详情。

此示例遇到 `extra_high` 风险时请求人工审批。审查器发生非超时故障时使用 `allow`，仍会执行剩余权限检查。需要其他错误策略时，选择 `deny` 或 `approval_required`。审查器超时始终拒绝执行。人工审批使用独立的 `tools.interaction_timeout_seconds` 超时。

快捷配置覆盖 Agent 的 shell 权限规则，包括显式的 `allow`、`deny` 或 `ask`。提供的审查字段替换对应 Agent 字段；无关规则和审查指令保留。省略的字段先继承 Agent 策略，再使用内置默认值。设置 `enable: false` 停止添加快捷配置；显式 Agent 策略仍然保留。默认值和 Codex Guardian 关联见 [shell 审查参考](configuration.md#shell-review-shortcut)。

### 使用 TypeSafe Jev 审查

要用 Jev 审查，创建下面的完整 Model 文件，并让 `TYPESAFE_API_KEY` 对 Harness UI 进程可用：

```yaml title="models/jev-review.yaml — 完整文件"
schema_version: "1"
kind: model
id: model-jev-review
name: Jev tool review
route: typesafe:jev-latest
authentication:
  kind: api_key
  env: TYPESAFE_API_KEY
```

将上一个用法中的 `security.shell_review.model` 设为 `model-jev-review`，验证后开始新 Run。对话 Agent 仍应使用支持文本的 Model。需要可重复的评估时，将 `jev-latest` 替换为经过测试的带版本 Jev ID。

自定义网关必须实现原生 TypeSafe 协议。要使用网关，在此 Model 中添加 `model_configuration.base_url: https://jev-gateway.example`；默认端点为 `https://api.typesafe.ai`。

Jev 将严重程度 0–3 映射为 `low`、`medium`、`high` 和 `extra_high`。置信度不改变决策。Jev 不提供文本解释。替换现有审查器前，先测试有代表性的命令。

## 开启 MCP 服务器

准备好可用的 MCP 端点及身份验证信息。下方 URL 是占位示例，不是公共测试服务器。

1. 让 `DOCS_MCP_TOKEN` 对启动 Harness UI 的进程可用。

2. 创建完整的服务器文件：

   ```yaml title="mcp/docs.yaml — 完整文件"
   schema_version: "2"
   kind: mcp_server
   id: mcp-docs
   name: Documentation server
   transport:
     url: https://mcp.example.com/mcp
     headers:
       Authorization: "Bearer ${DOCS_MCP_TOKEN}"
   ```

3. 将服务器 ID 加入 Agent 选择的服务器列表，保留其他仍需使用的 ID：

   ```yaml title="agents/coder.yaml — 合并到已有 Agent"
   mcp_servers: [mcp-docs]
   ```

4. 验证后，用该 Agent 创建新对话。尝试服务器中一个已知的只读工具，单独检查连接是否成功。

服务器文件只注册资源，选择它才会启用。初始化对话选择时，`mcp_servers: null` 继承默认值；`[]` 不选任何服务器。已有对话保留其 MCP 选择。本地命令传输或客户端式 JSON 见 [MCP 指南](mcp.md)。

## 开启 Skill

1. 在已有 Agent 的 `capabilities` 列表中添加以下条目：

   ```yaml title="agents/coder.yaml — 合并到 capabilities"
   capabilities:
     - capability: skills
       configuration: {}
   ```

2. 将 Skill 的 `SKILL.md` 放在可发现的来源中，例如 Project 的 `.agents/skills/<skill-name>/`。文件格式见 [Skill 指南](skills-and-content-plugins.md)。

3. 验证后开始下一次 Run。在终端中输入 `$` 查找该 Skill。

自动来源还包括用户 `~/.agents/skills`、已安装的 Content Plugin 和发行版内置配置 Skill。发现过程使用所选 Environment。显式的 `configuration.roots` 必须是 Environment 中的绝对路径；填入 Host 目录不会让 Sandbox 获得访问权限。见[来源优先级](skills-and-content-plugins.md#automatic-sources-and-precedence)。

## 修改显示与工具开关

只合并需要修改的字段：

```yaml title="a13n-harness-ui.yaml — 合并到 display 和 tools"
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

验证后重启，使显示默认值生效。要立即调整显示，使用 `/theme` 或 `/mode`；它们不改变模型推理或权限。

交互超时适用于终端中的问题、审批和外部结果，或整个 [WebUI 决策批次](webui.md#questions-and-approval-timeouts)。它不是模型执行期限，超时也不会授予审批。`enable_codeact: false` 会从新解析的 Run 中移除内置 CodeAct runner 和状态工具，即使 Agent 显式选择了该 Capability。允许范围见[根字段](configuration.md#display-settings)。

## 使用多个目录

只处理一个目录时，从该目录启动终端即可，不需要 Project 文件。处理多个目录时：

1. 创建 [Project 资源](environments-and-projects.md#project-file-reference)，按顺序填写需要的绝对根路径。
2. 验证配置。
3. 从 Project 的第一个根目录启动终端，开始工作前检查 `/status`。

用 `/environment` 修改执行模式。Project 根目录组织工作；Full Control 仍使用 Host 账户的权限。

## 修改为什么没有生效？

按顺序检查：

1. **文件位置：** `config path` 显示的是否是你编辑的配置树？
2. **有效性：** `config validate` 是否接受修改，且没有相关 Capability 警告？
3. **当前选择：** `/status` 是否显示目标 Agent 和 Model？必要时清除临时覆盖。
4. **生效时机：** 活动 Run 保留已捕获配置。普通资源修改在下一次 Run 中测试，默认选择在新对话中测试，进程和显示设置在重启后测试。

文件修改被拒绝后，上一版已接受配置可能继续生效。应修复错误，不要删除数据目录；该目录还保存对话和凭据。完整规则见[优先级与生效时机](configuration.md#what-wins-and-when-edits-apply)。
