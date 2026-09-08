# Use the terminal

## Everyday interaction

| Action                                              | Command or key                                   |
| --------------------------------------------------- | ------------------------------------------------ |
| Send the draft                                      | Enter                                            |
| Insert a newline                                    | Alt+Enter                                        |
| Complete a slash command or supported argument      | Tab                                              |
| Clear an idle draft; cancel active work             | Ctrl+C                                           |
| Exit from an empty draft                            | Ctrl+D                                           |
| Switch concise/detailed display                     | Ctrl+O or `/mode concise`, `/mode detailed`      |
| Explain commands                                    | `/help` or `/help command`                       |
| Start a new conversation without deleting history   | `/new`                                           |
| Search and preview saved conversations              | `/resume`                                        |
| Resume one saved conversation                       | `/resume session-id`                             |
| Read saved messages and tool details                | Ctrl+T or `/history`                             |
| List/select configured Agents                       | `/agent`, `/agent agent-codex`, `/agent default` |
| Read/change reasoning                               | `/thinking`, `/thinking low`                     |
| Toggle temporary priority service                   | `/fast`, `/fast on`, `/fast off`, `/fast reset`  |
| Read/change execution permissions                   | `/environment`, `/environment sandbox`           |
| Show current settings, usage, and pending decisions | `/status`                                        |
| Locate configuration and explain precedence         | `/config`                                        |
| Send additional guidance to the current Run         | Enter while running                              |
| Cancel active work                                  | `/cancel`                                        |
| Exit after cancelling and cleaning up active work   | `/quit` or `/exit`                               |

`/agent` selects an Agent's instructions, tools, shell-review policy, and default model for the next turn, while preserving conversation history and execution permissions. Create another Agent with `a13n-harness-ui add agent`.

`/model` opens a separate picker of configured Model resources. `/model <model-id>` temporarily overrides only the model without switching Agent or saving configuration. It applies to subsequent turns throughout this TUI session, including after `/new`, `/resume`, or `/agent`; restarting the TUI does not restore the override. `/model default` returns to the selected Agent's model. Selecting a Model or Agent clears temporary reasoning and service-tier settings. Both selection commands are unavailable during active work.

### Find a saved conversation

`/resume` opens a session browser without changing your current conversation or draft. Rows use a manual name, or the first saved input when unnamed, and are ordered by saved conversation activity. The selected row immediately previews its latest saved input and reply. These are bounded excerpts, not AI-generated summaries; unfinished replies are labeled as progress.

| Browser action                                                 | Key               |
| -------------------------------------------------------------- | ----------------- |
| Search names, IDs, and saved input/reply excerpts              | Type in Search    |
| Select a row                                                   | Up / Down         |
| Load another result page                                       | PageUp / PageDown |
| Toggle current directory / all directories                     | Ctrl+A            |
| Inspect selected session's retained messages without switching | Ctrl+T            |
| Edit its name; an empty name restores the first-input label    | F2, then Enter    |
| Refresh results after changes or an error                      | F5                |
| Resume the selected session                                    | Enter             |
| Cancel naming or return to the original conversation           | Escape            |

Search covers all matching saved metadata, not just the visible page, and treats `%` and `_` literally. The default scope includes all Projects whose first root matches this directory. All-directories mode also lets you inspect other, unresolved, or projectless sessions; it does not retarget their execution directory. For a session in another configured directory, Enter shows the launch command to use there. Active sessions cannot be resumed.

Search, naming, preview, and history inspection make no model requests. Closing history returns to the same browser selection. Cancelling the browser or a failed resume preserves composer text, cursor, folded pastes, and attachments. A successful resume retains the existing policy of discarding only unchanged prior attachments.

Existing conversations remain resumable after upgrade. Older sessions have no saved excerpts until subsequent execution; they remain searchable by name and ID, and Ctrl+T still reads their retained messages. The upgrade does not scan or rewrite conversation checkpoints.

### Read retained history

`--resume <id>`, `/resume <id>`, and confirmation in the browser restore recent messages as well as session settings. The initial screen is bounded to the latest 50 saved messages, at most 100 visible parts and 256 KiB, with explicit truncation notices. Live conversation display also evicts old content at 500 blocks or 2 MiB; this does not delete saved history. Press Ctrl+T to browse retained messages without mixing them into live output. Up/PageUp at the top loads an older page; Down/PageDown at the bottom loads a newer page. Home goes to the current page top, End reloads latest, and Ctrl+T, q, Escape or Ctrl+C closes the viewer and restores your draft. The viewer keeps one bounded page, not the whole conversation. It cannot recover messages already absent from the saved continuation after context compaction.

Type `$` to list available Skills with short descriptions, then filter and complete the name. `$name` can appear within an ordinary prompt; `/` is reserved for commands.

CLI Agents inherit the default cold-start filter: after 3600 seconds without model activity, native cold compression can shorten already-consumed tool results. This is separate from transcript display limits.

Bracketed multiline paste stays in the draft until Enter. Terminal support for Alt+Enter varies; terminals normally encode it as Escape followed by Enter. A complete command name such as `/ps` executes as a command with a brief acceptance notice. Unmatched input such as `/ps-like output needs clearer colors` stays exactly as typed and shows a notice that it is being treated as plain text. It becomes a message while idle, guidance during active work, or an answer to the current interaction, following the same rules as other text. Known commands with invalid arguments or unavailable state still show an error and preserve the draft; they never silently become a model prompt.

**Enter sends a message while idle, or adds text guidance while the agent is running.** The input hint changes with the current state. `/steer <message>` is still available as an optional explicit alternative. Guidance is appended through the current Harness Run's native input queue; the CLI does not reorder messages or maintain a separate next-turn queue. The CLI shows a short sent confirmation, then renders guidance as input when the model boundary applies it. It does not show queue receipts or raw enqueue events. Sending does not promise an immediate interruption of an in-flight tool or request. Normal idle input appears immediately, before admission; a rejection is explicitly marked and the draft remains recoverable.

Preparing, cancelling, or completed operations do not accept guidance. Rejected or unconfirmed input stays in the draft, or is available through `/recover` if you have started another draft. It is never silently redirected to a new Run or resent automatically. Active-run Enter with attachments preserves the entire draft because this CLI steering path accepts text only. In an approval or question selector, Enter confirms that interaction instead.

Commands preserve Windows backslashes. Quote paths containing spaces, such as `/attach "C:\\My Photos\\image.png"`. External tool results use the pending decision interface rather than raw slash commands.

**Concise** keeps provider-exposed thinking expanded without a repeated heading. Thinking flows directly into adjacent thinking and tool calls without extra blank rows. Authored paragraphs and separation from answers remain unchanged. Tool execution updates one compact block instead of adding separate argument and result headings. Expansion hints live in the footer, not under each tool. Shell calls use a single compact borderless row: the running/waiting state or observed exit code comes first, then the command. There are no stdout/stderr previews or empty output boxes in concise mode; press Ctrl+O for retained arguments, literal output, and full diagnostics. `shell_wait` shows the launch command when its same-Run process observation is still retained. Merged output such as `2>&1` remains stdout. Failure, incomplete/omitted output, and disclosure indicators stay inline before the command; routine process IDs and running/exited events do not create separate panels. A later background completion adds at most one lightweight, command-first notice while its correlation remains retained, and does not repeat a completion already shown by a tool result. Detailed arguments use formatted JSON. Specialized edit, summarize, and compact argument blobs are omitted from terminal blocks; their native events own the meaningful display. Successful edit and multi-edit operations replace the pending call with a file-path panel, added/removed counts, and an eight-line diff preview. Ctrl+O expands the full retained diff. The panel title owns the file path and change counts; the body omits repeated file and line-number headers, keeping context, additions, removals, and a gap marker between separate hunks. The native `FileEditAppliedEvent` supplies actual before/after file contents rather than proposed replacement snippets. Failed and no-op edits produce no applied-edit panel. Ordinary tool arguments and results remain folded; their full source is retained within the transcript display budget rather than replaced with a preview. **Detailed** expands retained tool blocks and shows subsequent child output. Ctrl+O switches without replaying events; `/history` reads saved details regardless of mode.

Summarize displays its body from the native `HandoffSummaryEvent` after the handoff summary has been persisted. This is the **prepared** state, distinct from **completed** when the handoff is consumed at the next boundary. The full available summary and file reminders are expanded, with lifecycle status separate from the body.

Compaction displays its generated summary in a distinct, independently expanded bordered panel. Shell, edit, summary, compaction, notes, and information panels share a subdued rounded frame and bold title. Diff additions and removals retain semantic colors; operation correlation remains in lifecycle details. The body comes from the native custom event, not inferred saved history or an assistant answer. Lifecycle metadata remains separate; neither summary visibility nor a completed lifecycle event asserts that a new continuation has been saved.

The status bar prioritizes state, **Fast** when requesting priority service, cumulative **tokens**, `ctx tokens (%)`, and observed Thread cache rate and model cost, followed by model and elapsed time as width permits. `tokens` sums input + output across all observed root Runs in this Thread, including restored usage after resume; cache counters are subsets and are not added again. It uses the same root-only scope as status-bar cache/cost, excluding child and auxiliary usage; `/usage` includes descendant usage. At every width, cumulative counts use one decimal place with uppercase units, such as `tokens 12.3K` or `tokens 1.2M`; values below 1K remain integers. Narrow terminals shorten the label to `tok` and omit lower-priority fields. `ctx` remains the latest request's context footprint, not that cumulative count. It refreshes on model-request usage reports while the task is still running, not only when the whole task finishes. It does not invent token-by-token usage before the provider reports it. Reasoning and settings stay in `/status`; input/output/cache breakdowns are in `/usage details`. Context percentage uses the last reported root request divided by your configured working budget. `--` means unavailable, not zero; genuine zero is `0%`. Child and auxiliary usage are not summed into it. `/status` shows exact context occupancy and complete settings in an aligned panel, without repeating the status bar.

Image, audio, video, and document inputs remain native Harness content. Terminal presentation shows compact media descriptions and available links, not base64. AG-UI clients also receive structured media references and caller metadata such as `image_object_id`, so a frontend can resolve its own content without the CLI returning image bytes.

### Tasks, usage, and terminal feedback

System feedback such as guidance delivery, command acceptance, fallback, and cancellation uses a consistent **System** label and subdued text, so it is distinct from assistant answers. Structured status/help content keeps its own readable formatting.

File-tool summaries (`view`, `write`, `edit`, `multi_edit`, and `ls`) and edit-panel titles use relative paths for files inside the directory where you launched Harness UI. Outside paths remain absolute. This only shortens the display; tools still receive their original paths, and expanded raw arguments/results remain unchanged.

A full-width divider separates the native task panel from tool output, even when collapsed. The panel shows up to five rows, active work first, with task IDs and dependency hints. F2 expands or collapses it. Counts and task status come from committed task facts and the selected continuation, not guesses from tool text. The panel is bounded so the composer stays usable on a short terminal.

A separated activity line above the status bar, outside the composer, shows the conversation's total subagent executions and observed running background processes. Lifecycle notices use subdued Activity text, not the user input marker. Commands inspect their details:

- `/subagents` lists up to 20 child executions from the App, including completed work; `/subagents next` reads the next page. State and Agent name are highlighted, while IDs and metadata are subdued. The detail header uses the same state colors; saved running state without local execution authority remains explicitly labeled unavailable rather than active.
- `/subagents <execution-id>` shows the existing child detail and retained-output preview without switching conversations.
- `/ps` shows up to 16 recent background-process observations: command, last reported status, process handle, and Run. Expand tool details for output. Foreground calls do not inflate the background count.

These commands work while the agent is running. Subagent totals come from saved App records; process counts come only from this conversation's bounded live observations, including child Shell calls. They are not a host-wide process inventory. A `+` count signals incomplete observations; unavailable status does not confirm a process exited. Starting or resuming another conversation clears process observations rather than inventing them from saved history.

`/status` focuses on Agent, Model, reasoning, context footprint, Environment, workspace, session, and display settings. The status bar reports observed root-Thread cache rate and model cost across Runs; `/usage details` exposes recorded counters. The bar's cache rate uses summed cache-read tokens / summed input plus output tokens, not a prompt-only hit rate or an average of Run percentages. Cache and cost persist across prompts, resume, and restart using the observed usage ledger; live reports update them during work, and completion reconciles retained observations even after failure, cancellation, or a live gap. Switching Threads replaces the totals; `/new` clears them. Context remains the latest root request footprint, not a Thread sum. Child and non-model usage are excluded from the bar. If any observed root request has unavailable cost, the total stays unknown rather than becoming a misleading known subtotal. No recorded model responses means unavailable coverage, not a fabricated zero. Subscription model cost is an estimate, not your subscription invoice.

`/usage` works with every model and shows a compact **observed Thread usage** summary: input/output tokens, response counts split into root and children, cache reads, known model cost and any unknown costs, and separate provider-currency subtotals. `/usage details` shows root/descendant/combined counters and the full input/output/cache/audio breakdown. The latest 32 Runs show unique contributions and their agent instance; older Runs remain in the totals. Per-model breakdowns are bounded to 32 names with an explicit remainder. Run grouping never adds inclusive parent totals on top of child usage, and a provider receipt is counted once per Thread family at its first observation.

This is recorded-so-far accounting, available during work and retained across compaction, resume, and restart, not a provider invoice. Older history without ledger records has unavailable coverage, not a fabricated zero. Context occupancy and subscription limits are separate. Startup automatically upgrades the local database; existing Thread history is preserved and is not guessed into historical usage.

For a Codex model, idle `/status` or `/usage subscription` shows each subscription window's **remaining percentage**, provider-reported local reset time, and available reset credits, without opening a menu. Missing limits are marked unavailable, not zero. Use `/usage reset` to review eligible credits: redeeming requires selecting an entitlement and explicitly confirming its account and redemption ID; **No** is the safe choice. A timeout or cancellation can leave the result unknown: keep this terminal open and use `/usage reset` to retry the same redemption ID. `/status` only reports the pending identity. A confirmed result remains confirmed even when the following usage refresh fails. OAuth token expiry is not a quota-reset time.

A terminal bell announces a completed Run or a new decision. While idle, the first Ctrl+C clears the draft and immediately explains how to exit; a second within two seconds exits. Editing disarms that exit confirmation. During work, Ctrl+C acknowledges cancellation immediately and waits for owned cleanup rather than exiting early.

Unexpected execution or terminal failures write a private temporary JSON diagnostic report with exception chains, frame locations, version, and session identity, but no locals or transcript. Nothing is uploaded. Review exception messages for sensitive data before attaching the report to an issue. The recovery hint includes custom configuration/data paths; only already saved state is resumable, not unsent input or unsaved in-flight changes.

### Local host commands

Enter `!command` to run a command yourself on the local POSIX host, for example `!git status`. This is user-owned shell execution in the CLI's working directory with the host process environment, **outside the model's selected Environment and Sandbox**. Selecting Sandbox for model tools does not sandbox `!command`. The command and its output are not injected into model context.

Local commands are accepted only while idle and outside interaction menus. A busy-state or menu rejection preserves the draft and attached files. Stdout and stderr are displayed as output events, followed by exit status and elapsed time. Commands are noninteractive: stdin receives EOF. Each command has a 120-second deadline and a combined 256 KiB output display limit; output beyond that limit is still drained rather than allowed to block the process.

Ctrl+C or `/cancel` terminates the owned process group and waits for cleanup before returning to idle. `!command` is explicitly unsupported on Windows until equivalent process-group cleanup is available; ordinary Windows CLI use and model tools remain supported under their existing execution contracts.

### Tool switches

Configure built-in tools in the root `a13n-harness-ui.yaml`:

```yaml
tools:
  enable_ask_user_question: true
  ask_user_question_timeout_seconds: 120
  enable_codeact: true
```

`ask_user_question` is enabled by default. Set `enable_ask_user_question: false` to omit it from later Runs. Each displayed question waits up to `ask_user_question_timeout_seconds` (a positive finite number). On timeout, the question call is returned as failed with an explicit no-answer message; no option is selected and no shell request is approved. A mixed batch still waits for its remaining decisions. `/cancel` keeps the request pending instead of returning a timeout.

CodeAct is enabled by default, providing the Harness's restricted Python `run_code` and `run_program` tools, plus `store`, `load`, and `forget` for explicit values retained in the saved continuation. Set `enable_codeact: false` to omit them from later Runs. This is not an unrestricted host Python shell: host effects still go through eligible tools and their ordinary policy. Advanced native runner settings can be authored with an Agent `codeact` Capability configuration. Both global switches take precedence over authored capability selections; existing tool visibility filters still apply. Accepted edits affect later Runs, not already captured execution. Question waiting policy applies only while the TUI is collecting answers; one-shot and HTTP callers retain their own interaction lifecycle.

### Approvals and questions

New starter Agents use `on_flagged: approval_required` and `on_error: skip`: non-timeout review failures alone do not open an approval prompt, but other tool-policy approval requirements still apply. Existing Agent settings are preserved.

Flagged shell commands and non-timeout review failures open a selectable prompt when the Agent's review policy requires approval. The panel puts risk and reason first, followed by the highlighted command and working directory. Detailed policy metadata stays behind **Inspect request details** or `/review request-id`, keeping the decision readable. Review the evidence before choosing **Approve once** or **Deny** in the selector below. No approval is preselected, and ordinary free text cannot approve a shell request. Truncated previews explicitly point to retained details.

AI shell review defaults to a 120-second deadline. A timeout automatically denies the command before execution and displays a timeout notice; it does not open another approval prompt. Human approval itself has no countdown.

Structured questions support single choice, multiple choice with Space, and typed answers. Complete option labels and descriptions remain scrollable above the compact selector, including in narrow terminals. Answers stay local until the complete batch is ready and are submitted against the exact continuation. `/cancel` discards local answers without approving anything; `/status` reopens pending decisions. Resuming a suspended conversation also reopens them. Approvals, denials, and external results use this interface, not `/approve`, `/deny`, or `/result` commands. Task tools, `ask_user_question`, and native note tools (`note_write`, `note_get`, `note_delete`) are included by default. Notes are injected into model context under the native budgets; explicit `notes_enabled: false` is respected. `/notes` shows saved full values in a dedicated panel, not 120-character previews, with explicit omissions above 256 notes or 256 KiB. Changed saved notes appear after each completed operation or resume as one compact row with the saved count and keys; Ctrl+O expands the values without repeating a large panel in the conversation. The shared subagent/background activity row keeps the saved note total visible, including notes omitted from the value preview. `/notes` is also discoverable in the footer; narrow activity rows prioritize counts over command hints.

### Theme, scrolling, paste, and attachments

- `/theme auto|dark|light` changes UI and Markdown syntax colors for this session; `display.theme` sets the file default. Auto preserves your terminal foreground/background and ANSI palette; passive metadata selects syntax variants without consuming input.
- PageUp/PageDown scroll the bounded display history; Ctrl+End returns to following live output. Ctrl+T browses retained messages after display eviction.
- Scroll mode (`/mouse on`) is the default. Wheel events belong to the hovered transcript, selector, or composer. In selectors, scrolling only browses; click highlights and Enter confirms. Ctrl+Space switches selector/composer keyboard focus. Esc closes an interaction/completion first, otherwise toggles scroll/select. Select mode (`/mouse off`) restores native selection/copy; application wheel routing is unavailable there. PageUp/PageDown and Ctrl+End work in either mode. In narrow conversation layouts, the input header shows the current `Scroll` or `Select` mode, and the footer shows the Esc switching action. Scrolling up freezes follow; returning to the bottom resumes it. Code rendering avoids padded backgrounds and OSC hyperlinks.
- Ctrl+V, Alt+V, or `/paste-image` explicitly reads clipboard images. Normal text paste remains text and never submits itself. Some terminals intercept Ctrl+V; use Alt+V or the command there.
- `/attach "path/to/image.png"` or `/attach "path/to/notes.txt"` is the portable file fallback. Local Linux clipboard images require access to a display session and its helper (`wl-paste` for Wayland or `xclip` for X11); no helper is installed automatically.
- Over SSH, normal text paste still works through your terminal (Cmd+V in a macOS terminal). Image paste reads the clipboard on the host running the TUI, not your local computer. Installing a clipboard helper on the remote host does not forward your local clipboard. Upload the image first, for example with `scp`, then use `/attach <remote-path>`. Pasting a Mac `/Users/...` path does not upload the file. Clipboard failures explain the SSH boundary or missing local display/helper without clearing the draft; SSH detection does not block a working clipboard, such as a forwarded display.
- Chips show draft files and images. `/remove 1` removes one; `/remove all` or idle Ctrl+C clears them. Up to eight attachments are accepted, with 10 MiB per file and 20 MiB total. PNG/JPEG/WebP/GIF images are validated and limited to 32 megapixels. Ordinary files remain available to the agent through its Thread file mount.
- Image-only and file-only prompts are supported. The selected model must support the submitted modality; failures never silently drop images or switch models. A failed pre-admission send restores its draft, or exposes `/recover` if you have already begun another draft. It is never resent automatically. A rejected `/new` or `/resume` also preserves attachments; failed commands restore their text or expose `/recover` without overwriting a newer draft.

### Long pasted text and Thread working files

Text pastes longer than 1000 characters fold into a compact `[Pasted text #N: ... chars]` marker. You can combine several blocks with your own instructions without filling the composer. Alt+E expands them for editing (moving the cursor inside a marker does the same); Backspace immediately after a marker (or Delete before it) removes the whole block. Enter sends the full text, never the placeholder. Text paste does not inspect your clipboard for images and does not submit by itself.

The agent has a `thread-files` Environment mount with `tmp/` for disposable downloads, scripts, conversions, and intermediate output. It belongs to the conversation, not an individual Run, and is reused after restarting the CLI. Submitted attachments are retained separately under `attachments/`; resuming a conversation does not depend on a clipboard temp file. Removing a draft chip does not delete a previously submitted file.

Temporary work is cleaned automatically at startup and hourly once it has been inactive for three days. An App conservatively protects every Thread directory it has used until it exits, including against cleanup by another local App. Cleanup never removes submitted attachments or Project files. Closing or archiving a conversation does not immediately delete its temporary directory. Do not store important final results in `tmp/`; ask the agent to copy them to your Project or another chosen destination.

In Sandbox mode, a command running in the Project cannot automatically read a sibling Thread mount. Attachment processing must select a working directory inside the Thread mount. Custom providers can expose this mount through file tools only; the model sees the available operations. There is no additional remote-host or network permission grant.
