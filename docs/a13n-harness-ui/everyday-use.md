---
title: Use the TUI
description: Start a task, guide the Agent, attach files, and return to saved work from the terminal.
---

## Everyday interaction

Complete [setup](setup.md), then open a terminal in the repository you want to work on:

```bash
a13n-harness-ui
```

1. Enter `/status` and check the Agent, Model, and `Workspace` directory.
2. Type a concrete task, such as `Explain how this repository runs its tests. Do not change files.`, and press Enter.
3. Read the response. Press Ctrl+O when you need tool arguments, output, or diffs.
4. Send a follow-up in the same conversation, or use `/new` for an unrelated task.

A conversation is a root Thread with saved history and settings. Each execution is a Run. The composer shows what Enter will do:

| Current state             | Enter does this                  |
| ------------------------- | -------------------------------- |
| Idle                      | Sends the draft as a new prompt  |
| Agent working             | Sends guidance to the active Run |
| Question or approval open | Confirms that interaction        |

Use Ctrl+J or Alt+Enter for a newline. Pasting multiline text leaves it in the draft until you send it.

### Choose an Agent or Model

While idle, use `/agent` or `/model` to open a picker. Run `/status` afterward to check the effective selection.

| Change                      | Command                                                   | Effect                                                                                   |
| --------------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Agent                       | `/agent <agent-id>`                                       | Changes instructions, tools, review policy, and the Agent's default Model for later Runs |
| Model                       | `/model <model-id>`                                       | Keeps the Agent; remembers a Model preference for this Project                           |
| Return to the default Model | `/model default`                                          | Clears the Project's Model preference                                                    |
| Reasoning                   | `/thinking`                                               | Shows or changes supported reasoning settings                                            |
| Temporary reasoning mode    | `/pro on`, `/pro off`, `/pro reset`                       | Overrides or restores the configured mode                                                |
| Temporary processing tier   | `/fast on`, `/fast off`, `/fast ultrafast`, `/fast reset` | Requests a supported tier or restores its default                                        |
| Execution permissions       | `/environment`                                            | Shows available Environment choices                                                      |

Agent and Model changes preserve history and clear temporary reasoning and service-tier overrides. A selected Model preference survives `/new`, Agent changes, and TUI restarts. A resumed Thread's saved default Model takes precedence over the launch Project's preference. Other open TUI processes keep their current choice until restart; a removed preference falls back to the Agent's Model. Explicit `--agent`, one-shot `run`, and API callers skip this TUI preference.

To create another Agent, follow [Create an Agent](agents-and-subagents.md#create-an-agent-from-files) or run `a13n-harness-ui add agent` outside the TUI.

### Input, display, and status

**Guide active work.** Type an additional requirement and press Enter, or use `/steer <message>`. Guidance can include attachments. By default it interrupts supported primary model generation; tool review and execution hand off cooperatively. Applied guidance appears in authored order in the transcript. For boundary-only delivery, configure [queued steering](configuration.md#built-in-tools-and-subagents).

**Recover rejected input.** Preparation, cancellation, and completed operations do not accept steering. Rejected or unconfirmed input stays in the draft; if you have started another draft, use `/recover`. Check the displayed outcome before resending: Harness UI never automatically sends that input to a new Run.

**Read more detail.** Ctrl+O switches between concise responses/tool summaries and retained arguments, output, and full diffs. It does not rerun tools. `/status` shows settings and pending decisions; `/help` explains commands. Type `$` to find a Skill and insert `$name` in your prompt.

**Use commands deliberately.** Tab completes commands and supported arguments. A recognized command with invalid arguments preserves the draft and shows an error. Unmatched slash text is sent unchanged as ordinary input with a notice. Quote paths containing spaces; Windows backslashes are preserved.

### Approvals and questions

When a decision opens, read its request before choosing an action:

1. Inspect the command, target, arguments, and available risk explanation. Use `/review <request-id>` for retained details.
2. Choose **Approve once**, **Deny**, or **Deny with reason** using the displayed controls.
3. If a reason editor opens, type the reason and press Enter. Use Ctrl+J/Alt+Enter for a newline or Esc to return.

Approvals have no preselected answer. **Approve with edited arguments**, when offered, requires a complete JSON object. Bound shell approvals cannot change arguments: deny with a reason and ask for a new command. Requests with omitted arguments cannot be approved.

A request for an external tool result offers **Provide result** instead. Supply the actual result in its JSON editor; this action does not execute the tool. Invalid JSON remains editable.

Each displayed decision uses `tools.interaction_timeout_seconds`, default 120 seconds. Editing does not reset the timer. A timeout returns a denial or a no-answer failure. Cancelling from the action selector leaves requests pending; `/status` or resume reopens them. Use these cards, not `/approve`, `/deny`, or `/result` commands. Shell review has its own model timeout and [configuration recipe](configuration-recipes.md#configure-shell-review).

#### Answer a question

A Question Card temporarily replaces the composer and preserves your ordinary draft and attachments.

1. Read the question and options. Up/Down or number keys focus an option without submitting it.
2. For a multiselect question, use Space to toggle choices. Press Enter to confirm.
3. To write your own answer, press Tab or Ctrl+Space. Enter submits the answer; Ctrl+J/Alt+Enter inserts a newline.
4. Answer any remaining questions. Look for the successful question-and-answer receipt, not just local collection, before treating the answers as accepted.

PageUp/PageDown scroll the card without choosing an answer. Clicking focuses an option; the wheel only scrolls. Esc in the answer editor returns to choices and keeps the answer draft. Esc from choices cancels collection and leaves requests pending. `/status` reopens them.

The answer editor accepts `/help`, `/mode`, `/status`, `/quit`, `/cancel`, `/theme`, `/mouse`, and `/review`. Other known commands are rejected; unknown slash-prefixed text remains literal answer text. Ctrl+O and Ctrl+T inspect tool details and history without answering. Closing history returns to the card.

### Theme, scrolling, paste, and attachments

To send a file with a task:

1. Enter `/attach "path/to/notes.txt"` or `/attach "path/to/image.png"`.
2. Wait for its loading marker to become a file or image marker.
3. Add instructions beside the marker and press Enter. Attachment-only messages also work.

For a clipboard image, explicitly press Ctrl+V or Alt+V, or use `/paste-image`. Ordinary text paste stays text. On Linux, image clipboard access needs a display session and `wl-paste` for Wayland or `xclip` for X11. Over SSH, the clipboard belongs to the machine running the TUI: upload your file first, then attach its remote path. Pasting a local path does not upload a file.

Markers such as `[image#1]` and `[file#2: notes.txt]` keep attachments in your authored order. Backspace/Delete removes a whole marker; Undo restores its content. Typing a marker's visible text or recalling it from input history does not attach a file. Delete a failed marker before retrying. The selected Model must support the input modality; a failure does not silently switch Models or discard images.

| Attachment limit | Value                     |
| ---------------- | ------------------------- |
| Count            | 8 per draft               |
| File size        | 10 MiB each, 20 MiB total |
| Image formats    | PNG, JPEG, WebP, GIF      |
| Image dimensions | Up to 32 megapixels       |

Use these controls to adjust reading and copying:

| Task                                   | Control                                           |
| -------------------------------------- | ------------------------------------------------- |
| Choose colors for this TUI             | `/theme auto`, `/theme dark`, `/theme light`      |
| Set the persistent theme               | `display.theme` in root configuration             |
| Scroll output                          | PageUp / PageDown                                 |
| Follow live output again               | Ctrl+End, or return to the bottom                 |
| Route mouse wheel to TUI panels        | `/mouse on`                                       |
| Use terminal-native selection and copy | `/mouse off`                                      |
| Switch mouse modes                     | Esc, after any open interaction/completion closes |

Auto theme keeps the terminal's foreground/background and ANSI palette. Keyboard scrolling works in either mouse mode. Use Ctrl+T when older output has left the live display.

### Long pasted text and Thread working files

Pastes longer than 1000 characters fold into compact markers. Press Alt+E, or move the cursor into a marker, to edit the text. Backspace after a marker or Delete before it removes the whole block. Enter submits the complete authored text to the App.

A separate [long-text input policy](configuration.md#long-text-inputs) can deliver blocks longer than 8000 Unicode characters as retained text-file references to the Model. Folding changes the editor display; file conversion changes model delivery.

The Agent receives a `thread-files` mount for work associated with this conversation:

| Location            | Purpose and lifetime                                                                    |
| ------------------- | --------------------------------------------------------------------------------------- |
| `attachments/`      | Submitted files retained for later Runs and resume                                      |
| `tmp/`              | Disposable scripts, downloads, and intermediate output; reused across Runs and restarts |
| `tmp/tool-results/` | Oversized tool output; Run-private and subject to cleanup when that Run ends            |

Temporary files can be pruned after three days of inactivity, checked at startup and hourly. An App protects Thread directories it has used until it exits. This cleanup leaves attachments and Project files intact. Ask the Agent to copy important results to your Project or another chosen destination before relying on them.

In Sandbox, a shell in the Project cannot automatically access the sibling Thread mount. Attachment processing must use a working directory inside that mount. Custom providers may expose it through file tools only.

### Find a saved conversation

1. Start the TUI in the directory where you want to work.
2. Enter `/resume`. Search by name, ID, or saved input/reply excerpt.
3. Select a row to preview it. Ctrl+T inspects history without switching.
4. Press Enter to resume, then `/status` to check the working directory and selections.

**Resume uses the launch directory's Project and roots**, while keeping saved history, Agent, and execution permissions. To retain the original Project and directories, launch from that Project's first root. An active conversation cannot be resumed in another TUI.

| Browser action                                          | Key               |
| ------------------------------------------------------- | ----------------- |
| Select a conversation                                   | Up / Down         |
| Load another results page                               | PageUp / PageDown |
| Include other directories and projectless conversations | Ctrl+A            |
| Rename; empty name restores the first-input label       | F2, then Enter    |
| Refresh results                                         | F5                |
| Return without switching                                | Escape            |

Rows are ordered by saved activity. Previews are saved excerpts, with unfinished replies marked as progress. Search, rename, and history inspection make no model requests. Older conversations without excerpts remain searchable by name and ID. Cancelling or failing resume preserves your draft and attachments.

If you know the ID, use `/resume <thread-id>` in the TUI or `a13n-harness-ui --resume <thread-id>` from your shell.

### Read retained history

Press Ctrl+T or enter `/history`. Use Up/PageUp for older pages and End for the latest. Ctrl+T, q, or Escape closes history and returns to your draft.

Saved display history is separate from model context. Summarization, compaction, and cold-start compression can shorten the context without deleting retained display content. A saved checkpoint covers only its saved position; newer live output can still be unsaved.

### Tasks, usage, and terminal feedback

Use the inspection command for the question you need answered. These commands also work while the Agent is busy:

| Question                                        | Command                                                      |
| ----------------------------------------------- | ------------------------------------------------------------ |
| Which settings and decisions are active?        | `/status`                                                    |
| What did a child Agent return?                  | `/subagents`, `/subagents next`, `/subagents <execution-id>` |
| Which background processes were observed?       | `/ps`                                                        |
| How much recorded usage belongs to this Thread? | `/usage` or `/usage all`                                     |
| How much belongs to one scope?                  | `/usage root`, `/usage subagents`                            |
| What are the exact costs and coverage?          | `/usage details`                                             |

Process observations are bounded and local to this conversation, not a Host-wide process list. Unavailable does not mean exited. Usage combines observed root, child, and auxiliary-model requests; `ctx` is the latest root request's context footprint. `--` means unavailable, and `+` marks incomplete data or partial known costs. Recorded usage is an estimate, not a subscription invoice.

For a Codex Model, idle `/usage subscription` shows available subscription windows and reset information. `/usage reset` lets you review and explicitly confirm an eligible redemption. If the result is unknown after timeout or cancellation, keep the TUI open and retry the same redemption ID. For a ChatGPT Model, Harness UI instead directs you to manage usage in ChatGPT; it does not provide numeric subscription allowance.

**Tool executions** groups retained-tool checks and results. Completion while idle can trigger an Agent continuation without sending or clearing your draft. Stop cancels that continuation; `/executions cancel <execution-id>` targets the retained tool itself. A cancellation request is not proof that work has stopped.

Ctrl+C during work requests cancellation and waits for cleanup. While idle, the first Ctrl+C clears the draft; a second within two seconds exits. Editing disarms that exit gesture. Ctrl+D on an empty draft, `/quit`, or `/exit` also exits. The terminal bell announces completion or a new decision.

If a failure produces a private diagnostic report, inspect its exception messages for sensitive data before sharing it. Reports are not uploaded automatically. Follow the printed resume hint to return to saved state; unsent drafts and unsaved work are not recovered by that hint.

### Tasks and notes

Task and note tools are included by default. F2 expands or collapses the task panel, showing up to five saved rows with active work first. `/notes` opens saved note values; Ctrl+O expands note updates. The note panel shows up to 256 entries or 256 KiB and identifies omissions.

To disable notes for an Agent, set `configuration.notes_enabled: false` in its `working_state` Capability entry. Saved task and note changes become visible after an operation or resume.

### Work toward a Goal

Use a Goal when you want the same Agent to check its work against an explicit completion criterion:

```text
/goal Implement the requested change, run its tests, and report any remaining blockers.
```

The initial response is check `0`; the default budget allows 10 additional checks. Watch the Goal bar for progress and outcome. Ctrl+C and `/cancel` still stop execution, and ordinary tool permissions still apply.

The Agent signals completion with `[GOAL_COMPLETE]` on its own line. After summarization or compaction it must audit the original objective again. **Verified is the Agent's own completion claim, not an independent review.** Exhaustion, cancellation, errors, and an unverified stop remain distinct outcomes.

Set root `max_goal_iterations` to change the budget for new Goals; zero or a negative value disables follow-up checks. A suspended Goal keeps its captured budget. Resuming a conversation alone does not restart unfinished work. Send ordinary input for normal behavior, or `/goal` for a new objective and budget. WebUI exposes the same policy through its [Goal controls](webui.md).

### Local host commands

While idle on a POSIX host, enter a command such as:

```text
!git status
```

This runs in the TUI's working directory with the Host process environment, **outside the Agent's selected Environment and Sandbox**. Its command and output stay out of model context. Windows does not support this TUI shortcut; ordinary Windows CLI use and Agent tools remain available.

Commands are noninteractive, with stdin closed, a 120-second deadline, and a 256 KiB combined output display limit. Ctrl+C or `/cancel` terminates the owned process group and waits for cleanup. Busy work or an open interaction rejects the command while preserving your draft.

### Tool switches

To change built-in interaction tools, merge this fragment into the [selected root YAML](configuration.md#locate-and-validate-your-files):

```yaml
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
```

Both tools default on. Save the file, run `a13n-harness-ui config validate` outside the TUI, and use a later Run for the changed selection. Set either enable flag to `false` to omit that tool. The interaction timeout must be positive and finite; it controls active TUI waiting, not durable HTTP decision expiry.

CodeAct provides restricted Python `run_code`/`run_program` and saved values through `store`/`load`/`forget`. Its Host effects use the eligible tools and their normal permissions. Advanced runner settings belong in the Agent's `codeact` Capability; root switches and visibility filters take precedence.
