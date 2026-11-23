---
title: 配置配方
description: Harness UI 开箱即用的 YAML 配置配方，涵盖推理深度与上下文预算、Shell 命令审查、MCP 服务器以及工具超时。
---

将以下片段放入 Harness UI 配置树（`a13n-harness-ui config path`），并用 `a13n-harness-ui config validate` 校验。

## 调整模型、推理深度或上下文预算

在 `models/` 下创建或编辑模型文件：

```yaml title="models/primary.yaml"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  credential_ref: key-primary
settings:
  openai_reasoning_effort: medium
  max_tokens: 16384
model_characteristics:
  context_window_tokens: 200000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

## 配置 Shell 审查

将 `environment.shell_exec` 命令交由审查模型评估，并在达到高风险级别时请求人工审批：

```yaml title="a13n-harness-ui.yaml — 合并到根文件"
security:
  shell_review:
    enable: true
    reviewer_model: model-reviewer
    ask_at_or_above: extra_high # low | medium | high | extra_high
```

该快捷开关会覆盖根运行与子运行的 Shell 规则。设为 `enable: false` 可关闭快捷开关，同时保留智能体显式配置的审查策略。默认值见[Shell 审查快捷开关](configuration.md#shell-review-shortcut)。

## 调整工具超时与 CodeAct

```yaml title="a13n-harness-ui.yaml — 合并到根文件"
tools:
  enable_ask_user_question: true
  enable_codeact: true
  interaction_timeout_seconds: 120
```

`interaction_timeout_seconds` 适用于 TUI 和 WebUI 的问题/审批批次（见[问题与审批超时](webui.md#questions-and-approval-timeouts)）。将 `enable_codeact` 设为 `false` 可全局禁用 CodeAct。

## 排查无效配置编辑

若 YAML 编辑未通过校验，Harness UI 会继续使用上一份有效配置。运行 `a13n-harness-ui config validate` 查看具体出错的文件和字段——切勿删除数据目录，其中还保存了历史对话和凭据。详见[优先级与生效时机](configuration.md#what-wins-and-when-edits-apply)。
