---
title: Set up your first Agent
description: Connect a model, create an Agent, and choose execution permissions.
---

Setup connects a Model, creates an Agent, and selects execution permissions. It runs automatically when no Model is configured. To run it again, leave chat and use:

```console
a13n-harness-ui setup
```

There is no `/setup` command in chat. Explicit setup returns to your shell after saving; first-use setup opens chat.

## 1. Connect a Model

Choose a supported subscription or API-key connection.

| Connection         | What you provide                               | Where credentials live                                  |
| ------------------ | ---------------------------------------------- | ------------------------------------------------------- |
| Codex subscription | Compatible login                               | Shared Codex account store                              |
| Grok subscription  | Compatible login                               | Shared Grok account store                               |
| API key            | Provider/protocol, model ID, and key reference | An environment variable or Harness UI's local key store |

Existing compatible subscription logins are detected and can be reused. If login is missing, setup shows the external `a13n-harness-ui login codex` or `a13n-harness-ui login grok` command, then offers recheck. Login runs outside the terminal UI; there is no `/login` chat command. Malformed or unsupported stores receive repair guidance, not automatic replacement.

For API keys, setup offers a hidden key field. You can also supply `env:OPENAI_API_KEY` or `key:key-primary`. Never paste a key into the chat composer. A newly entered key is saved immediately to the separate local key store, not to Model YAML.

[Models and authentication](models-and-authentication.md#subscription-login-and-api-keys) covers login methods, stored keys, and account locations.

## 2. Select the model and settings

Choose from the installed starter catalog or enter a case-sensitive API model ID. The catalog is bundled guidance, **not** a live entitlement or availability check. Existing Model resources retain their settings rather than being silently migrated to new defaults.

For a new Codex connection, setup also offers Fast or Standard service. Fast requests priority; it may consume more quota and does not guarantee speed. Use `/fast` for a temporary change or edit the Model for a permanent one.

Native image/audio/video declarations are saved for known model routes. They tell Harness which inputs to send; they do not add a modality that the actual endpoint lacks. Verify custom endpoints before copying declarations.

See [Model settings](models-and-authentication.md#native-request-settings), [context budgets](models-and-authentication.md#context-and-modality-policy), and [Fast mode](models-and-authentication.md#fast-mode-and-service-tiers).

## 3. Choose execution permissions

- **Full Control:** commands run with your host account's filesystem and network authority. No Envd runtime is required.
- **Sandbox:** setup checks local isolation prerequisites before saving. Failure does not fall back to Full Control. Built-in Windows execution supports Full Control only.

Your selection saves configuration without an additional confirmation step. Read [execution permissions](environments-and-projects.md#execution-permissions) before choosing.

The working directory is the terminal workspace. Setup does not require a Project file or create a default `project-local` resource.

## Advanced setup

```console
a13n-harness-ui setup --advanced
```

Advanced setup exposes optional context, reasoning, shell-review, subagent, and instruction choices. Normal setup supplies starter values; these are still editable YAML, not hidden application state.

Normal setup includes three built-in child roles and, if absent, enables `security.shell_review` at the `extra_high` threshold. It uses a configured Model to review shell launches and asks for approval when flagged. Existing review settings are preserved. This review is not isolation and does not inspect every command; see [tool-review configuration](configuration-recipes.md#configure-tool-review) for the exact policy.

Built-in subagents inherit the parent Model. Advanced setup offers all or none; edit `subagents.include` to choose individual roles. External Codex/Claude Code subagent import is a separate `/import` workflow, not part of setup.

## Add another connection or Agent

```console
a13n-harness-ui add model
a13n-harness-ui add agent
```

`add model` creates a reusable Model and offers a separate Agent flow. `add agent` lets you reuse a Model or create a new one, then name the Agent. Reuse references the existing Model without copying or editing it. Repeated names allocate separate identities; existing defaults and conversations remain unchanged.

Use `/agent` to switch the complete Agent. Use `/model` to temporarily try a configured Model while keeping the current Agent's instructions and tools.

## Cancel or recover setup

Use Up/Down and Enter, or type option numbers. Esc goes back; Ctrl+C or Ctrl+D cancels. Cancelling first-use setup returns to the shell without opening chat.

A completed login or saved key remains even if setup is cancelled. If publishing several files fails, inspect the completed paths shown in the error before retrying. Keep any `.a13n-harness-ui-setup-recovery-*` file until you review its original content.

## Inspect what was saved

The generated root configuration explicitly enables [WebUI Sidekick](configuration.md#webui-sidekick):

```yaml
webui:
  sidekick: {}
```

This does not start work automatically. Set `sidekick: null` or choose **Settings → General → Sidekick → Disabled** to turn off the preference. Repeating setup preserves an explicit opt-out or custom Agent/Model selections.

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

Continue with [configuration recipes](configuration-recipes.md) or the [complete root reference](configuration.md#complete-root-document).
