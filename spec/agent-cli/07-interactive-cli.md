# Interactive CLI

## Design Position

Agent CLI is a full-terminal client of `AgentUiApp`. The App remains the sole owner of configuration acceptance, execution, continuation selection, decisions, cancellation, and observations. Terminal code does not construct Harness Runs, Environment mounts, or database transactions. A future browser or remote transport adapter can reuse the App boundary; this release does not start a server, daemon, or subprocess terminal. The CLI uses one native prompt-toolkit Application on Windows, macOS, and Linux.

The terminal owns one current conversation and an editable draft. Durable Thread and Project identities remain internal application concepts. Removing their management UI does not delete or migrate historical execution data.

## Startup and Terminal Ownership

`a13n-cli` without a subcommand starts interactive mode in the invocation's current directory. Input and output must both be terminals; automation uses `a13n-cli run <prompt>`. CLI help, version, and command parsing do not import the App, Harness, database, provider libraries, or terminal renderer.

The first editable prompt precedes heavy imports and App preparation. Background preparation does not issue a model request, authenticate an account, launch an Environment, or connect configured MCP servers merely to draw the landing screen. Enter during preparation preserves the draft without submitting it. Local help, display mode, and exit remain available. Errors are visible without replacing the terminal shell.

One process owns input and terminal rendering for its lifetime and restores the terminal on exit. A scrollable Markdown viewport retains a bounded semantic display cache, not a second conversation database. Blocks reflow on resize and theme changes. Eviction and truncation are visible; `/history` reads durable App history. Streaming updates are coalesced, and completed blocks are not reparsed on each delta. Interleaved root and child messages remain distinct. Finalization and cancellation flush pending text. Scrolling away from the latest output freezes the view until explicitly returning to the latest output. Best-effort event loss is distinguished from a complete live transcript; the committed final answer remains authoritative.

The composer, pending interaction, status, and transcript share one layout. Narrow or short terminals preserve usable input. Dark, light, and passive automatic themes are available; terminal detection never consumes keyboard input. Mouse capture can be disabled for native text selection and copy. Clipboard images are read only on an explicit paste action, with file attachment as a portable fallback. Multiline text paste never triggers image reads or submission.

Enter submits; Alt+Enter inserts a newline; bracketed multiline paste remains a draft until explicit submission. Ctrl+C clears an idle draft or requests active cancellation. Ctrl+D on an empty draft exits. Rejected commands and busy submissions preserve input. Unknown slash input is never sent to a model.

## Commands and Presentation

One command catalog owns syntax, aliases, help, completion, and availability during active work. Duplicate names or aliases are rejected. Commands that would change subsequent execution configuration are unavailable while work is active. `/mode`, `/status`, `/help`, `/cancel`, and `/quit` remain available.

- **Concise** is the default: assistant text, decisions, errors, and necessary completion/recovery results.
- **Detailed** additionally shows provider-exposed reasoning, tool names and arguments, file-edit tool calls, bounded tool results, and child output. It does not invent reasoning or expose hidden model state.
- `/mode concise|detailed` and Ctrl+O switch live presentation. Previously displayed content is unchanged; `/history` reads retained details explicitly.

Display mode never changes tool access, shell review, model reasoning effort, or approval behavior. The compact status bar identifies model, reasoning effort, last reported request footprint, working context budget, elapsed time, state, and display mode. Unknown usage is shown as unknown, not zero. Request footprint is not cumulative Run usage and does not estimate the next prompt. Child and auxiliary request usage are not added to root context occupancy.

## Workspace and Resume

On the first submission, the App resolves the canonical invocation directory. It reuses an exact, single-root internal Project or publishes a deterministic directory-derived Project through normal validated configuration mutation. It does not reuse a containing ancestor or silently widen the workspace to multiple roots. It never rewrites an old Project's roots to follow a new invocation directory. Concurrent creation can reuse a verified identical resource; collisions or unrelated configuration changes fail without overwriting user files.

Launch resume cannot be combined with Agent, Environment, or title overrides; the CLI rejects the combination before startup rather than silently discarding a requested permission boundary.

`/new` clears the current selection and starts a new conversation on the next submission, preserving all history. `/resume` lists saved root sessions in the current workspace; `/resume <id>` and `--resume <id>` reject archived, child, active, and cross-workspace sessions. Resume uses the durable selected continuation, not discarded partial output. `/history [cursor]` reads bounded retained pages without maintaining a duplicate terminal transcript database.

## Per-operation Model Selection

`/model` selects a configured Model resource for subsequent operations. `/thinking` changes reasoning effort independently from display mode. These selections are local to the interactive session and are passed as detached `RunModelOverrides` values; they do not rewrite YAML or sticky Thread configuration. Selecting a model resets reasoning to that model's configured default. `default` clears the corresponding override.

The root coordinator copies options before scheduling execution. Composition capture selects the accepted resource generation at the existing capture boundary, validates the effective settings, and records the complete effective Model recipe before native construction. A receipt alone does not claim that capture has finished. Inheriting Markdown children receive the effective parent model; explicit child and auxiliary selections remain independent. Invalid selections fail without fallback. Previous immutable compositions remain unchanged.

Resume restores the last selected continuation's Model ID and reasoning effort for subsequent operations, resolving them against current accepted resources. It does not restore stale credential bytes or silently resurrect deleted resources.

## Setup and Context Management

When no usable model is configured, the CLI automatically opens selectable setup and preserves any draft typed during preparation. Setup separates BYOK API-key access from BYOS subscription access, then configures the coding Agent and offers optional external subagent migration. Selectable steps accept arrow keys, numeric choices, or exact values; text fields retain explicit defaults. Back revisits selections; cancellation restores the original conversation draft. Setup previews publication before explicit confirmation. Cancellation before publication writes no selected resource files. Existing edited files remain preserved; stale and partial publication follow the shared setup contract. Configuration values are explicit, editable fields rather than opaque preset references.

Codex defaults to `openai-codex:gpt-5.6-sol`, high reasoning, and a 350,000-token working context budget. Setup offers:

| Choice   | Working budget | Meaning                                                                  |
| -------- | -------------: | ------------------------------------------------------------------------ |
| standard |        272,000 | Current catalog default; conservative local budget                       |
| balanced |        350,000 | Repository-work recommendation aligned with the reference YAACLI profile |
| extended |        872,000 | Current catalog maximum; not an account entitlement guarantee            |

The local budget does not change provider limits. Generated Model resources declare `model_characteristics.context_window`, `proactive_context_management_threshold: 0.65`, and `compact_threshold: 0.90`. They are captured and supplied through native `HarnessModelCharacteristics`. Generated coding Agents select the native runtime-context, handoff, and compaction capabilities. At 350,000 tokens the derived reminder and compaction thresholds are 227,500 and 315,000. Explicit capability policies can override their native derived defaults.

The last root request's reported usage controls the native thresholds; accumulated usage does not. Codex subscription requests do not receive an API `max_tokens` cap copied from YAACLI presets. Provider-specific settings remain subject to the supported model adapter and native subscription constraints.

## Multimodal Drafts

The CLI uses the [App's native multimodal input contract](05-runtime-subagents-and-surfaces.md#process-local-operations). It sends actual `BinaryContent`, never a local path disguised as image content. Image-only input is supported without inventing a user prompt.

The composer retains at most eight images, at most 10 MiB per image and 20 MiB in aggregate. PNG, JPEG, WebP, and GIF are accepted after decoding validation. Oversized or invalid images do not alter the draft. Attachment chips identify pending images and support explicit removal. Clipboard acquisition runs off the input loop, is fenced to its originating draft, and never inserts a late image into a submitted, cleared, or replaced draft. A failed pre-admission submission preserves text and images for explicit retry; once admitted, receipt recovery governs the outcome and the CLI does not automatically resend input.

## Decisions, Cancellation, and Recovery

Pending approvals and external results are presented using App-owned deferred request types. Individual terminal answers are local drafts until every request in the exact continuation batch has a response. Unknown IDs, mismatched kinds, invalid JSON, or stale continuations are rejected. Cancelling, exiting, or leaving setup never implies approval. Structured question results remain subject to the same App/Harness validation as other adapters.

Cancellation goes through the root receipt and waits for execution/environment cleanup. Exit cancels active work before closing the App in its owning task. Login cancellation uses the existing credential-store transaction boundary; a completed store write is not reported as rolled back. No cancellation retries a potentially side-effecting operation automatically.

## Verification

Tests cover lazy imports, real-terminal draft preservation and paste, bounded Markdown streaming and resize/theme reflow, live mode switching, exact-cwd binding, model overrides in immutable composition, configuration round trips, typed decision batches, cancellation/cleanup, and durable resume. Provider calls in tests use isolated stores and mock Models; terminal tests do not authenticate real accounts.
