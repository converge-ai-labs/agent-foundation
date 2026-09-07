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
| List recent conversations in this workspace         | `/resume`                                        |
| Resume one saved conversation                       | `/resume session-id`                             |
| Read saved messages and tool details                | `/history`, then the next-page command it prints |
| List/select configured Models                       | `/model`, `/model model-codex`, `/model default` |
| Read/change reasoning                               | `/thinking`, `/thinking low`                     |
| Read/change execution permissions                   | `/environment`, `/environment sandbox`           |
| Show current settings, usage, and pending decisions | `/status`                                        |
| Locate configuration and explain precedence         | `/config`                                        |
| Send additional guidance to the current Run         | Enter while running                              |
| Cancel active work                                  | `/cancel`                                        |
| Exit after cancelling and cleaning up active work   | `/quit` or `/exit`                               |

Bracketed multiline paste stays in the draft until Enter. Terminal support for Alt+Enter varies; terminals normally encode it as Escape followed by Enter. An unknown slash command is never sent to the model.

**Enter sends a message while idle, or adds text guidance while the agent is running.** The input hint changes with the current state. `/steer <message>` is still available as an optional explicit alternative. Guidance is appended through the current Harness Run's native input queue; the CLI does not reorder messages or maintain a separate next-turn queue. The CLI shows a short sent confirmation, then renders guidance as input when the model boundary applies it. It does not show queue receipts or raw enqueue events. Sending does not promise an immediate interruption of an in-flight tool or request. Normal idle input appears immediately, before admission; a rejection is explicitly marked and the draft remains recoverable.

Preparing, cancelling, or completed operations do not accept guidance. Rejected or unconfirmed input stays in the draft, or is available through `/recover` if you have started another draft. It is never silently redirected to a new Run or resent automatically. Active-run Enter with images preserves the entire draft because this CLI steering path accepts text only. In an approval or question selector, Enter confirms that interaction instead.

Commands preserve Windows backslashes. Quote paths containing spaces, such as `/attach "C:\\My Photos\\image.png"`. External tool results use the pending decision interface rather than raw slash commands.

**Concise** keeps provider-exposed thinking expanded without a repeated heading. Tool execution updates one compact block instead of adding separate argument and result headings. Expansion hints live in the footer, not under each tool. Shell commands have a few more preview lines; detailed arguments use formatted JSON. Specialized edit, summarize, and compact argument blobs are omitted from terminal blocks; their native events own the meaningful display. Successful edit and multi-edit operations show expanded diffs from the native `FileEditAppliedEvent`, using the actual before/after file contents rather than proposed replacement snippets. Failed and no-op edits produce no applied-edit panel. Ordinary tool arguments and results remain folded; their full source is retained within the transcript display budget rather than replaced with a preview. **Detailed** expands retained tool blocks and shows subsequent child output. Ctrl+O switches without replaying events; `/history` reads saved details regardless of mode.

Summarize displays its body from the native `HandoffSummaryEvent` after the handoff summary has been persisted. This is the **prepared** state, distinct from **completed** when the handoff is consumed at the next boundary. The full available summary and file reminders are expanded, with lifecycle status separate from the body.

Compaction displays its generated summary in an independently expanded Markdown block, correlated to the operation ID. The body comes from the native custom event, not inferred saved history or an assistant answer. Lifecycle metadata remains separate; neither summary visibility nor a completed lifecycle event asserts that a new continuation has been saved.

The status bar prioritizes state, model, observed model cost, input/output tokens, `ctx N%`, reasoning effort, elapsed time, and cache counters as width permits. Context percentage uses the last reported root request divided by your configured working budget. `--` means unavailable, not zero; genuine zero is `0%`. Child and auxiliary usage are not summed into it. `/status` shows exact counters and complete settings even in a narrow terminal.

Image, audio, video, and document inputs remain native Harness content. Terminal presentation shows compact media descriptions and available links, not base64. AG-UI clients also receive structured media references and caller metadata such as `image_object_id`, so a frontend can resolve its own content without the CLI returning image bytes.

### Tasks, usage, and terminal feedback

F2 expands or collapses the native task panel. Counts and task status come from committed task facts and the selected continuation, not guesses from tool text. The panel is bounded so the composer stays usable on a short terminal.

`/status` reports observed root-Run input, output, cache-read, cache-write, and cost counters. These are process-local observations, not reconstructed billing totals; child and non-model usage are excluded. An unavailable cost stays unknown rather than becoming zero. Subscription model cost is an estimate, not your subscription invoice.

For a Codex model, idle `/status` also reads subscription limits, provider-reported reset times, and available reset credits. **Refresh usage** is read-only. Redeeming a credit requires selecting the entitlement and then explicitly confirming its account and redemption ID; **No** is the safe choice. A timeout or cancellation can leave the result unknown: keep this terminal open and use `/status` to retry the same redemption ID. A confirmed result remains confirmed even when the following usage refresh fails. OAuth token expiry is not a quota-reset time.

A terminal bell announces a completed Run or a new decision. While idle, the first Ctrl+C clears the draft and immediately explains how to exit; a second within two seconds exits. Editing disarms that exit confirmation. During work, Ctrl+C acknowledges cancellation immediately and waits for owned cleanup rather than exiting early.

Unexpected execution or terminal failures write a private temporary JSON diagnostic report with exception chains, frame locations, version, and session identity, but no locals or transcript. Nothing is uploaded. Review exception messages for sensitive data before attaching the report to an issue. The recovery hint includes custom configuration/data paths; only already saved state is resumable, not unsent input or unsaved in-flight changes.

### Local host commands

Enter `!command` to run a command yourself on the local POSIX host, for example `!git status`. This is user-owned shell execution in the CLI's working directory with the host process environment, **outside the model's selected Environment and Sandbox**. Selecting Sandbox for model tools does not sandbox `!command`. The command and its output are not injected into model context.

Local commands are accepted only while idle and outside interaction menus. A busy-state or menu rejection preserves the draft and attached images. Stdout and stderr are displayed as output events, followed by exit status and elapsed time. Commands are noninteractive: stdin receives EOF. Each command has a 120-second deadline and a combined 256 KiB output display limit; output beyond that limit is still drained rather than allowed to block the process.

Ctrl+C or `/cancel` terminates the owned process group and waits for cleanup before returning to idle. `!command` is explicitly unsupported on Windows until equivalent process-group cleanup is available; ordinary Windows CLI use and model tools remain supported under their existing execution contracts.

### Tool switches

Configure built-in tools in the root `a13n-harness-ui.yaml`:

```yaml
tools:
  enable_user_input: true
  user_input_timeout_seconds: 120
  enable_codeact: false
```

`ask_user_question` is enabled by default. Set `enable_user_input: false` to omit it from later Runs. Each displayed question waits up to `user_input_timeout_seconds` (a positive finite number). On timeout, the question call is returned as failed with an explicit no-answer message; no option is selected and no shell request is approved. A mixed batch still waits for its remaining decisions. `/cancel` keeps the request pending instead of returning a timeout.

CodeAct is disabled by default. Set `enable_codeact: true` to enable the Harness's restricted Python `run_code` and `run_program` tools, plus `store`, `load`, and `forget` for explicit values retained in the saved continuation. This is not an unrestricted host Python shell: host effects still go through eligible tools and their ordinary policy. Advanced native runner settings can be authored with an Agent `codeact` Capability configuration. Both global switches take precedence over authored capability selections; existing tool visibility filters still apply. Accepted edits affect later Runs, not already captured execution. Question waiting policy applies only while the TUI is collecting answers; one-shot and HTTP callers retain their own interaction lifecycle.

### Approvals and questions

Flagged shell commands and failed shell reviews open a selectable prompt. Inspect the tool, request, arguments, and review details before choosing **Approve once** or **Deny**. No approval is preselected. The Inspect action and `/review request-id` read retained details when the preview is truncated. Ordinary free text cannot approve a shell request.

Structured questions support single choice, multiple choice with Space, and typed answers. Complete option labels and descriptions remain scrollable above the compact selector, including in narrow terminals. Answers stay local until the complete batch is ready and are submitted against the exact continuation. `/cancel` discards local answers without approving anything; `/status` reopens pending decisions. Resuming a suspended conversation also reopens them. Approvals, denials, and external results use this interface, not `/approve`, `/deny`, or `/result` commands. Task tools and `ask_user_question` are included by default; notes remain opt-in.

### Theme, scrolling, copy, and images

- `/theme auto|dark|light` changes UI and Markdown syntax colors for this session; `display.theme` sets the file default. Auto preserves your terminal foreground/background and ANSI palette; passive metadata selects syntax variants without consuming input.
- PageUp/PageDown scroll the bounded display history; Ctrl+End returns to following live output. `/history` retrieves durable pages after display eviction.
- Scroll mode (`/mouse on`) is the default. Wheel events belong to the hovered transcript, selector, or composer. In selectors, scrolling only browses; click highlights and Enter confirms. Ctrl+Space switches selector/composer keyboard focus. Esc closes an interaction/completion first, otherwise toggles scroll/select. Select mode (`/mouse off`) restores native selection/copy; application wheel routing is unavailable there. PageUp/PageDown and Ctrl+End work in either mode. Scrolling up freezes follow; returning to the bottom resumes it. Code rendering avoids padded backgrounds and OSC hyperlinks.
- Ctrl+V, Alt+V, or `/paste-image` explicitly reads clipboard images. Normal text paste remains text and never submits itself. Some terminals intercept Ctrl+V; use Alt+V or the command there.
- `/attach "path/to/image.png"` is the portable fallback. Linux clipboard images need `wl-paste` or `xclip`; no helper is installed automatically.
- Chips show draft images. `/remove 1` removes one; `/remove all` or idle Ctrl+C clears them. Up to eight validated PNG/JPEG/WebP/GIF images are accepted, with 10 MiB per image, 20 MiB total, and 32 megapixels per image.
- Image-only prompts are supported. The selected model must support the submitted modality; failures never silently drop images or switch models. A failed pre-admission send restores its draft, or exposes `/recover` if you have already begun another draft. It is never resent automatically. A rejected `/new` or `/resume` also preserves attachments; failed commands restore their text or expose `/recover` without overwriting a newer draft.
