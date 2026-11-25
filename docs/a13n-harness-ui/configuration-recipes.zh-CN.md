---
title: 常用配置配方
sidebarTitle: 常用配方
description: 开箱即用的 Harness UI YAML 配置配方，涵盖默认智能体、推理与上下文预算、OpenAI 兼容网关、子智能体、Shell 审查、MCP 与技能。
---

先运行 `a13n-harness-ui config path` 确认配置根目录（默认为 `~/.a13n-harness-ui/a13n-harness-ui.yaml`），在该目录下应用以下任一配方，并执行 `a13n-harness-ui config validate` 校验。

## 切换默认智能体、模型或上下文预算

在 `a13n-harness-ui.yaml` 中设置新会话的默认智能体（`defaults: {agent: agent-coder}`），保存 API 密钥（`a13n-harness-ui auth key set key-primary`），并在 `models/` 下创建模型文件：

```yaml title="models/primary.yaml"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  credential_ref: key-primary # 或 env: OPENAI_API_KEY
settings:
  thinking: high
model_characteristics:
  context_window_tokens: 128000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

在智能体文件（`agents/coder.yaml`）中设置 `model: model-primary`。若当前项目曾通过 `/model` 记住其他模型覆盖，可在终端执行 `/model default` 恢复默认。

## 接入 OpenAI 兼容网关

使用 `openai-chat:`（若网关实现 Responses 协议则用 `openai-responses:`），并将接口基地址填入 `model_configuration.base_url`：

```yaml title="models/compatible.yaml"
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

## 配置 Shell 命令风险审查

让审查模型（或 TypeSafe Jev）在根运行与子运行执行 `environment.shell_exec` 前自动评估风险等级（详见 [Shell 审查快捷开关](configuration.md#shell-review-shortcut)）：

```yaml title="a13n-harness-ui.yaml — 合并到 security"
security:
  shell_review:
    enable: true
    risk_threshold: extra_high
    model: model-review
    on_flagged: approval_required
    on_error: allow
```

若使用 TypeSafe Jev 作为 `model-review`，新建一个 `route: typesafe:jev-latest` 且 `authentication: {kind: api_key, env: TYPESAFE_API_KEY}` 的模型资源即可。

## 根据 MCP 工具标注请求审批

给智能体添加权限能力，即可在调用 MCP 服务器标为破坏性的工具前请求审批。如果已配置 `ToolPermissionsCapability`，将此字段合并到现有配置中：

```yaml
capabilities:
  - capability: ToolPermissionsCapability
    configuration:
      respect_mcp_annotations: true
```

该设置默认为 `false`。启用后，继承权限的工具若标注了 `readOnlyHint: true`，则直接放行；若标注了 `destructiveHint: true` 且未标为只读，则请求审批。其他工具沿用来源默认权限。服务器标注仅作提示；需要强制执行的策略请写成显式规则。

## 启用内置子智能体、MCP 服务器与技能

- **内置子智能体（`explorer`、`code-reviewer`、`executor`）：** 在 `a13n-harness-ui.yaml` 中合并 `subagents: {include: [explorer, code-reviewer]}`（`[]` 关闭自动注入；可通过 `a13n-harness-ui config subagents --format json` 查看）。
- **为智能体挂载 MCP 与技能：** 先注册 `mcp/docs.yaml`（[MCP 指南](mcp.md)），再将 `mcp_servers` 与 `skills` 合并进智能体 YAML（将 `SKILL.md` 放入 `.agents/skills/<name>/`，详见[技能](skills-and-content-plugins.md)）：

```yaml title="agents/coder.yaml — 合并到现有智能体"
mcp_servers: [mcp-docs]
capabilities:
  - capability: dynamic_environment
    configuration: {files_enabled: true, shell_enabled: true}
  - capability: skills
    configuration: {}
```

## 调整显示选项、工具超时与多目录项目

```yaml title="a13n-harness-ui.yaml — 合并到 display 与 tools"
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

多仓库协作可在 `projects/*.yaml` 中声明有序根目录（见[项目配置](environments-and-projects.md#project-file-reference)）。若修改未生效，请依次核对 `config path`、运行 `config validate`（校验失败会保留上一版生效配置，切勿删除包含会话与凭据的数据目录）、用 `/status` 检查临时覆盖，并开启新一次运行。
