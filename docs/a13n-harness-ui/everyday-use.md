---
title: Use the TUI
description: Efficiently navigate codebases, guide agent thinking, attach files, review tool approvals, and resume saved work in the terminal.
---

Launch the Harness UI Terminal Interface (TUI) inside any repository directory after completing [setup](setup.md):

```bash
a13n-harness-ui
```

Run `/status` to verify your active Agent, Model, and workspace, enter a task, and press **Ctrl+O** to toggle full tool arguments, execution outputs, and diffs.

## Composer behavior, shortcuts, and switches

The **Enter** key adapts to the current Run state:

| State             | Pressing Enter                                            | Key composer & view shortcuts                                                                                                |
| :---------------- | :-------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------- |
| **Idle**          | Submits your draft as a new turn                          | **Ctrl+J** / **Alt+Enter**: newline; **Ctrl+G**: edit draft in `$VISUAL`/`$EDITOR`; **Tab**: autocomplete; `$`: Skill picker |
| **Agent working** | Injects live Steering guidance into the active Run        | **Ctrl+O**: expand/collapse tool details; **F2**: toggle task panel; **Ctrl+C**: cancel Run; **Ctrl+End**: jump to live tail |
| **Decision open** | Confirms your choice on the active approval/question card | **Ctrl+T** / `/history`: full transcript; **Alt+E**: inspect folded paste (`> 1,000` chars); **Ctrl+D** / `/exit`: quit      |

Switch models, reasoning tiers, and runtime inspection on the fly (see the full [Command reference](command-reference.md)):

| Task                      | Command                                                                      |
| :------------------------ | :--------------------------------------------------------------------------- |
| **Switch Agent or Model** | `/agent <agent-id>`, `/model <model-id>`, `/model default`                   |
| **Reasoning & speed**     | `/thinking`, `/pro on\|off\|reset`, `/fast on\|off\|reset`                   |
| **Inspect runtime**       | `/status`, `/environment`, `/subagents`, `/ps`, `/notes`, `/usage [details]` |
| **Display & drafts**      | `/theme auto\|dark\|light`, `/mouse on\|off`, `/recover`                     |

## Approvals and questions

When a sensitive tool call or `ask_user_question` prompt suspends execution, an interactive decision card opens:

- **Tool approvals:** Review the tool name, arguments, and risk summary, then select **Approve once**, **Deny**, or **Deny with reason** (typing feedback so the model adjusts its plan).
- **Structured questions:** Highlight choices with **Up/Down** (press **Space** to toggle multi-select options) and press **Enter**, or press **Tab** to enter custom free-text answers.

Customize built-in interaction switches in your root or Project YAML and verify with `a13n-harness-ui config validate`:

```yaml
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
```

## Attachments, saved threads, Goals, and host shell

- **Attach files and screenshots:** Run `/attach "path/to/file"` or paste clipboard images with **Ctrl+V** / `/paste-image`. Thread working files live under `attachments/` (preserved across turns), `tmp/` (reused across turns), and `tmp/tool-results/` (cleaned after Run completion).
- **Resume past conversations:** Run `/resume` to browse threads (**Up/Down**, **PageUp/PageDown**, **Ctrl+A** for all projects, **F2** to rename, **Enter** to load).
- **Autonomous multi-turn Goals:** Prefix iterative objectives with `/goal` so the agent executes, verifies, and audits completion criteria across turns:
  ```text
  /goal Refactor the database connection pool, run the test suite, and resolve any regressions.
  ```
- **Direct host shell commands:** Prefix commands with `!` (such as `!git status` or `!pytest -q`) to run them immediately in your local shell without spending model tokens.
