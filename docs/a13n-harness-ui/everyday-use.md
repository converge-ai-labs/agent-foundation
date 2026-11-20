---
title: Use the TUI
description: Efficiently navigate codebases, guide agent thinking, attach files, review tool approvals, and resume saved work in the terminal.
---

The **Harness UI Terminal Interface (TUI)** is built for developers who live in their terminal. It combines the power of modern LLMs with local shell workflows, giving you instantaneous control over file inspection, git reviews, and tool execution without context switching.

## Everyday interaction

Launch the TUI inside any repository directory after completing [setup](setup.md):

```bash
a13n-harness-ui
```

1. Run `/status` to verify your active Agent, Model, and current workspace directory.
2. Enter a task (e.g., `Review the recent commits and explain the test strategy.`) and press Enter.
3. Read the incoming response. Press **Ctrl+O** to expand full tool arguments, execution outputs, and git diffs.
4. Continue the dialogue in the same thread, or type `/new` to initiate a clean task.

The composer prompt adapts its behavior dynamically to the current run state:

| Active state | Action performed by pressing Enter |
| :--- | :--- |
| **Idle** | Submits your draft prompt as a new task turn |
| **Agent working** | Injects guidance (Steering) directly into the active run |
| **Decision open** | Confirms your selection on the active approval or question card |

Use **Ctrl+J** or **Alt+Enter** to insert newlines. Multiline text can also be pasted directly.

To draft extensive prompts using your favorite editor, press **Ctrl+G** in the composer. Harness UI opens `$VISUAL` (or `$EDITOR`, such as `nvim` or `code --wait`), allowing you to compose complex instructions with full editor ergonomics. Saving and closing the editor returns your draft seamlessly to the composer.

### Choose an Agent or Model

Quickly inspect or switch models and agents on the fly:

| Task | Command | Effect |
| :--- | :--- | :--- |
| **Switch Agent** | `/agent <agent-id>` | Switches instructions, capabilities, toolsets, and child roles |
| **Switch Model** | `/model <model-id>` | Switches active model provider; saves preference for this project |
| **Reset Model** | `/model default` | Restores the default model configured in the project |
| **Toggle Thinking** | `/thinking` | Configures reasoning token effort and thinking parameters |
| **Pro reasoning tier** | `/pro on`, `/pro off`, `/pro reset` | Overrides or restores the high-effort reasoning tier |
| **Speed processing tier** | `/fast on`, `/fast off`, `/fast reset` | Toggles ultra-fast inference mode when supported |
| **Check Environment** | `/environment` | Inspects current sandbox and mount settings |

### Input, display, and status

- **Steer active work:** While the agent is generating output, you can type steering instructions and press Enter (or use `/steer <message>`). Harness immediately injects your correction into the active run, guiding the model without aborting prior progress.
- **Inspect tool details:** Toggle **Ctrl+O** to switch between concise summary views and comprehensive diagnostic traces containing raw command arguments, return values, and full diffs.
- **Recover unsent drafts:** If you close an editor or cancel an action, your unsent prompt is preserved in the draft buffer. Use `/recover` if you need to restore an earlier buffered draft.
- **Autocomplete commands:** Press **Tab** to view autocomplete options for slash commands, available models, and registered skills. Type `$` to trigger the Skill picker.

### Approvals and questions

When the agent executes sensitive commands or needs user clarification, it displays an interactive decision card:

1. Review the tool name, arguments, and visual risk summary.
2. Choose **Approve once**, **Deny**, or **Deny with reason** using the arrow keys.
3. If providing a denial reason, type your feedback and press Enter so the model can adjust its strategy.

#### Answer a question

When the agent presents a structured question card:

1. Use **Up/Down** arrow keys to highlight an option.
2. For multiselect questions, press **Space** to toggle choices, then press **Enter** to confirm.
3. To provide custom text, press **Tab** to switch to the free-text input field, type your answer, and press **Enter**.

### Theme, scrolling, paste, and attachments

Harness UI supports rich file and media attachments directly in the terminal:

1. Type `/attach "path/to/screenshot.png"` or `/attach "docs/notes.md"`.
2. Wait for the file marker (e.g., `[image#1]`) to render in the draft.
3. Add prompt text alongside the attachment and press Enter.

For clipboard images, press **Ctrl+V** or type `/paste-image` to attach screenshots effortlessly.

| Feature | Shortcut / Command | Action |
| :--- | :--- | :--- |
| **Theme selection** | `/theme auto`, `/theme dark`, `/theme light` | Adjusts color palette to match your terminal emulator |
| **Terminal scroll** | **PageUp** / **PageDown** | Scrolls historical run output |
| **Jump to live output** | **Ctrl+End** | Returns immediately to active generation output |
| **Mouse routing** | `/mouse on`, `/mouse off` | Toggles mouse wheel scrolling vs native terminal text selection |

### Long pasted text and Thread working files

When pasting snippets longer than 1,000 characters, Harness UI folds the text into an expandable marker. Press **Alt+E** with the cursor on the marker to inspect or edit the full block.

Each conversation receives a dedicated thread directory for managing attachments and temporary files:

| Folder | Lifecycle | Content |
| :--- | :--- | :--- |
| `attachments/` | Preserved across turns | User-attached files and images |
| `tmp/` | Reused across turns | Intermediate build artifacts and downloads |
| `tmp/tool-results/` | Cleaned up on run completion | Oversized command outputs and raw dumps |

### Find a saved conversation

To browse or resume past conversations:

1. In the TUI, enter `/resume`.
2. Use **Up/Down** to navigate past conversation threads. A preview panel displays recent prompt excerpts.
3. Press **Enter** to load the selected thread and continue immediately.

| Shortcut in `/resume` | Action |
| :--- | :--- |
| **Up / Down** | Browse threads |
| **PageUp / PageDown** | Page through history |
| **Ctrl+A** | Toggle display of threads across all projects |
| **F2** | Rename the selected thread |
| **Escape** | Close picker and return to the active draft |

### Read retained history

Press **Ctrl+T** or enter `/history` to open the full conversation transcript reader. Use **PageUp** and **PageDown** to review prior turns, or press **Escape** to return to your composer.

### Tasks, usage, and terminal feedback

Query real-time metrics and background status at any time, even while the agent is actively generating:

| Query | Command | Information returned |
| :--- | :--- | :--- |
| **System status** | `/status` | Active agent, model, working directory, and pending approvals |
| **Subagent status** | `/subagents` | Status and results of spawned child agents |
| **Running processes** | `/ps` | Active background shell commands and daemons |
| **Token usage** | `/usage` | Accumulated prompt/completion tokens and estimated cost |
| **Usage breakdown** | `/usage details` | Per-turn cost and model breakdown |

Press **Ctrl+C** once to cancel active generation gracefully; pressing **Ctrl+D** on an empty draft or typing `/exit` exits the application.

### Tasks and notes

Press **F2** to expand or collapse the task progress panel. The panel displays current todo items, in-progress tasks, and completed checklist goals. Type `/notes` to inspect working memory notes recorded by the agent.

### Work toward a Goal

When you have a multifaceted objective that requires iterative verification (e.g., implementing a feature, running unit tests, fixing errors, and re-running checks), launch a Goal:

```text
/goal Refactor the database connection pool, run the test suite, and resolve any regressions.
```

In Goal mode, the agent iteratively executes changes and audits the result against your objective across multiple turns. The goal progress bar tracks iteration counts. The agent reports completion when all criteria are satisfied or explains any external blockers encountered.

### Local host commands

To execute quick shell commands in your project directory without sending tokens to the model, prefix your command with an exclamation mark:

```bash
!git status
!cargo test
```

Commands execute immediately in the host shell environment, displaying output directly in your terminal panel.

### Tool switches

You can customize built-in tool configurations by modifying your project's configuration YAML:

```yaml
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
```

Save the file and run `a13n-harness-ui config validate` to confirm syntax. CodeAct and question asking are enabled by default for maximum interactivity.
