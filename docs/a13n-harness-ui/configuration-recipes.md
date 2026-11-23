---
title: Configuration recipes
description: Ready-to-use YAML recipes for Harness UI covering reasoning and context budgets, shell command review, MCP servers, and tool timeouts.
---

Copy these snippets into your Harness UI configuration tree (`a13n-harness-ui config path`) and validate with `a13n-harness-ui config validate`.

## Change the Model, reasoning, or context budget

Save or edit a Model file under `models/`:

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

## Configure shell review

Route `environment.shell_exec` commands through a reviewer Model and require approval for high-risk commands:

```yaml title="a13n-harness-ui.yaml — merge into root file"
security:
  shell_review:
    enable: true
    reviewer_model: model-reviewer
    ask_at_or_above: extra_high # low | medium | high | extra_high
```

This shortcut overrides the Agent's shell rule across root and child Runs. Set `enable: false` to disable it while keeping any explicit Agent-level review policies. See [Shell review shortcut](configuration.md#shell-review-shortcut) for defaults.

## Tune tool timeouts and CodeAct

```yaml title="a13n-harness-ui.yaml — merge into root file"
tools:
  enable_ask_user_question: true
  enable_codeact: true
  interaction_timeout_seconds: 120
```

`interaction_timeout_seconds` applies to question/approval batches in TUI and WebUI (see [Questions and approval timeouts](webui.md#questions-and-approval-timeouts)). Setting `enable_codeact: false` disables CodeAct globally.

## Troubleshooting invalid edits

If a YAML edit fails validation, Harness UI keeps the last accepted configuration active. Run `a13n-harness-ui config validate` to see the exact file and field error—do not delete the data directory, which also stores conversations and credentials. See [What wins and when edits apply](configuration.md#what-wins-and-when-edits-apply).
