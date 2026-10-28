---
title: Set up your first Agent
description: Connect a model account, choose where tools may run, and verify your first response.
---

By the end of this guide, you will have an Agent that can answer a message. Start with [Harness UI installed](installation.md) and either a supported model subscription or a provider API key.

Launch from the directory you want to work in:

```console
cd your-repository
a13n-harness-ui
```

Replace `your-repository` with your directory. The setup wizard opens when the default Agent is missing or has no Model. If you have already configured an Agent and want to run the wizard again, quit the terminal interface and run `a13n-harness-ui setup` from your shell. In WebUI, open the login link printed by `a13n-harness-ui webui` and use its setup screens.

## 1. Connect a Model

A **Model** is a saved connection to a model provider. Choose one connection for this first session:

- **Subscription:** choose ChatGPT, Codex, Grok, or GitHub Copilot. Reuse a detected account or follow the displayed sign-in instructions. Complete sign-in before continuing if you want to test a response now.
- **API key:** choose the provider or protocol and enter your key in the wizard's hidden key field. For a custom endpoint, also provide its API base URL and use the protocol it supports.

An API key entered here is saved separately from the Model YAML. If you already manage credentials, enter `env:OPENAI_API_KEY` to read an environment variable or `key:key-primary` to reuse a stored key. The environment variable must be available to the process launching Harness UI. Put keys in the dedicated field, never in a conversation message.

You can choose to configure a connection now and sign in later with `a13n-harness-ui login <provider>`. The [authentication guide](models-and-authentication.md#subscription-login-and-api-keys) covers each provider's login method and credential storage.

## 2. Select the model and settings

Choose a suggested model or enter its case-sensitive API model ID. Use a model your account can access; the suggestions come from a catalog, not an account-access test. When you reuse an existing Model, its saved settings stay unchanged.

For a first session, keep the offered model settings unless you have a specific requirement. You can change [reasoning and context budgets](models-and-authentication.md#context-and-modality-policy) later. For a Codex connection, **Fast** requests priority service and may use more quota; it does not guarantee a faster response.

API-key setup also offers an optional **Shell review model**. Select a model to review shell commands, or skip to leave review off. Selecting one creates a separate Model using the same endpoint and key reference. Each review makes an additional model request. Shell review and execution isolation are separate controls.

## 3. Choose execution permissions

This choice determines the authority available to file and command tools:

| Choice           | Use and effect                                                                                                             |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Full Control** | Tools run with your Host account's filesystem and network authority. Direct execution needs no Envd download.              |
| **Sandbox**      | Setup prepares local isolation and checks its prerequisites before saving. A failed check does not switch to Full Control. |

Read the [platform requirements](environments-and-projects.md#execution-permissions) when choosing Sandbox. Windows support uses experimental native MXC isolation.

In the terminal wizard, completing the choices saves the configuration. In WebUI, wait for the **Workspace** checks to finish, then select **Save and start chatting**. Sandbox preparation may download and verify Envd; its progress and failures appear beside the execution choice and in the footer. You can cancel preparation or retry a failed check.

You do not need to create a Project file. The terminal uses its launch directory and selects or creates a single-root Project when you send the first prompt.

## 4. Verify a response

First-use terminal setup opens the composer. If you ran the explicit `setup` command, it returns to your shell; launch `a13n-harness-ui` again. WebUI opens an empty conversation after saving. Saving alone sends no model request.

Send a message that does not need file or shell tools:

```text
Reply with one short greeting. Do not use tools.
```

A reply confirms that this selected Model can handle a request. Next, try a read-only task in your repository:

```text
Read the README and summarize what this project does. Do not modify files.
```

If the greeting fails, check the selected model ID and authentication before troubleshooting tools. If the greeting works but the file task fails, check the working directory and [Environment permissions](environments-and-projects.md). The [everyday-use guide](everyday-use.md) explains follow-ups, stopping work, and opening saved conversations.

## Inspect what was saved

Quit the terminal interface before running these shell commands:

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

`config path` locates the selected configuration and data paths. The root YAML holds defaults; sibling `models/` and `agents/` directories hold the Model and Agent resources. `config validate` checks the files and references. It does not send a model request, so keep the response test above as a separate check.

Setup also enables [WebUI Sidekick](configuration.md#webui-sidekick) for new configurations with `webui.sidekick: {}`. To disable it, choose **Settings → General → Sidekick → Disabled** or set `sidekick: null`. Setup preserves an existing choice, and this preference alone starts no work.

## Add another connection or Agent

Once the first Agent works, use either command from your shell:

```console
a13n-harness-ui add model
a13n-harness-ui add agent
```

`add model` creates a reusable Model and can lead into a separate Agent flow. `add agent` lets you reuse a Model or create one, then name the Agent. Reusing a Model references it without copying or editing it. Existing defaults and conversations keep their selections.

In the terminal, use `/agent` to switch the whole Agent. Use `/model` to change the Model for the current Project while retaining the Agent's instructions and tools; `/model default` clears that selection. For file-based changes, continue with [configuration recipes](configuration-recipes.md).

## Advanced setup

Run `a13n-harness-ui setup --advanced` to choose optional context, reasoning, shell-review, subagent, and instruction settings during setup. The resulting settings are editable YAML.

Normal setup includes three built-in Subagent roles. They inherit the parent Model; advanced setup offers all or none, and `subagents.include` selects individual roles later. Importing Codex or Claude Code roles uses the separate `/import` workflow.

When shell review is selected, setup initializes absent review settings to review shell launches and request approval at `extra_high` risk. API-key setup without a reviewer leaves the shortcut disabled. Codex setup creates a separate reviewer Model and enables Guardian credit linking. Existing review settings remain unchanged. See [shell review](configuration-recipes.md#configure-shell-review) to change the reviewer or policy.

Setup saves known media-input capabilities for the selected route. Before adding image, audio, or video support to a custom endpoint, verify that endpoint's actual support; see [Model input policy](models-and-authentication.md#context-and-modality-policy).

## Cancel or recover setup

In the terminal wizard, use Up/Down and Enter, or type option numbers. Esc goes back; Ctrl+C or Ctrl+D cancels. Cancelling first-use setup returns to the shell.

Completed logins and saved keys remain available after cancellation. If a multi-file save fails, inspect the paths in the error before retrying. Legacy `.a13n-harness-ui-setup-recovery-*` directories are left untouched.
