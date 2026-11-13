---
title: Bot
description: Keep objectives moving with a personal assistant that coordinates working Threads and checks progress every hour.
---

Bot is the instance-wide assistant in the WebUI sidebar. Give Bot an ongoing objective. Bot can find or create Projects, start ordinary working Threads, and inspect their saved results on later heartbeats. Its conversation uses the existing Thread reader, editor, and native panels.

## Start and configure Bot

1. Open **Bot** in the sidebar. Bot is off by default. Its inline setup has independent Agent and Model selectors, both set to **Default** initially. Agent follows the global default Agent; Model follows the selected Agent's Model.
2. Optionally add custom instructions, such as your preferred coordination style. Custom instructions are appended alongside the built-in instructions and do not replace them. Captured global `AGENTS.md` guidance also applies.
3. Choose **Turn on Bot**. On first usable enable, the server sends a visible preset user message asking Bot to discover and explain your workspace. This uses an ordinary Model request and may incur cost. You can continue the conversation immediately. The first Heartbeat is scheduled one interval later, even if you never send a message. Edit later in **Settings → General → Bot**.

The default heartbeat interval is **one hour**. Opening the page starts no model request. The first workspace introduction runs once, independently of Heartbeat and browser presence. Existing chat history or a previous attempt prevents repetition after restart or re-enabling; a failed attempt stays visible and requires an explicit follow-up. The WebUI server must remain running for heartbeat processing. Bot is shared by instance-key participants and is unavailable to scoped guests. [Live mode](live-mode.md) is independent.

Merge these fields into the selected root YAML's existing `webui` mapping:

```yaml title="a13n-harness-ui.yaml — Bot"
webui:
  bot:
    enabled: true
    agent: null # Follow defaults.agent; an existing Agent ID selects its execution settings
    model: null # Follow the selected Agent's Model; an existing Model ID overrides it
    instructions: "Prefer coordinating work in project Threads."
    heartbeat_enabled: true
    interval_seconds: 3600
    connector_interval_seconds: 60
```

Bot inherits the selected Agent's execution settings: Capabilities (including compaction and handoff), tools and permissions, subagents, Plugins and MCP selections. It replaces that Agent's role instructions with built-in Bot policy and your optional Bot instructions. Native Files, Shell and Computer tools are enabled under the selected execution policy and Environment limits. Browser uses the existing Host Browser when enabled. Structured `ask_user_question` stays disabled; clarification uses ordinary chat. Null or omitted `agent` follows `defaults.agent`; an explicit existing Agent ID selects a different execution base. Null or omitted `model` follows that selected Agent's Model; an explicit existing Model ID overrides it independently. Changing Agent preserves an explicit Model choice. Without an effective Agent, an explicit Bot Model uses the minimal built-in settings. Invalid Agent or Model references reject the configuration update, retaining the previous accepted configuration. A missing effective Model prevents execution. Changes apply to later Runs; captured Runs keep their original configuration.

Heartbeat intervals accept 60–86,400 seconds. Connector delivery intervals accept 1–86,400 seconds. Custom instructions accept up to 1,048,576 characters. Validate file edits with `a13n-harness-ui config validate`.

## Coordinate work and memory

Bot has an exclusive `bot/` workspace beside the selected root YAML. With the default configuration, this is `~/.a13n-harness-ui/bot/`. An explicit `--config` changes that parent directory. This remains Bot's default working directory, including after opening a Project Environment. Bot completes short, bounded tasks directly with native tools and delegates long-running implementation, research or operations to working Threads. Use this folder for coordination artifacts rather than treating it as your work Project.

Bot can access all configured Project Environments directly without creating a Thread just to reach their files or tools:

1. Use `list_environment_targets` to discover targets captured for the current Run.
2. Use `open_environment_target` with the chosen target ID.
3. Use the returned virtual paths for Files and explicit aliases for Shell or Computer. Bot's default directory stays unchanged.

Each Project target uses its configured roots, local profile, Device bindings and default. The Project's Agent, MCP selections and Run Extensions do not replace Bot's execution policy. Connections open only when the target is used; one unavailable target does not block unrelated work. Separate targets retain separate Sessions, even when they point to the same directory. Configuration changes affect later Runs. The `host-desktop` target provides Host Computer Use when enabled and available. Browser remains Host-owned and independent of Project targets. Existing tool permissions and Environment limits still apply; access does not authorize consequential work.

Keep always-applicable workspace guidance in `bot/AGENTS.md`. When present, its contents are automatically included as bounded file context on every Bot Run, alongside global `AGENTS.md` guidance.

Bot manages recurring checks in `bot/HEARTBEAT.md`. Each heartbeat tells Bot to read the current file if it exists and follow its checklist. The contents are read on demand rather than automatically injected. Bot can create and edit the file to track checks, priorities, and follow-up criteria, and remove obsolete items. You can edit it too. A missing file does not block a heartbeat. File edits change future checks without triggering a Run; heartbeat timing still comes from Settings.

Bot can use `create_project` to create a normal Project configuration and `create_thread` to start work with normal Sidekick/global Agent defaults. Project creation, Thread creation, Run admission, and task completion are separate outcomes. Bot is instructed to inspect saved results before reporting success.

When memory is enabled, Bot uses the existing memory tools for global memory and its own `bot` scope in `memory/bot/` beside the root YAML. It keeps `MEMORY.md` as a concise workspace map and stores durable Project understanding with source references and uncertainty in topic files. Heartbeats check relevant, changed or unknown Projects in bounded passes. No separate project-profile database or memory extraction pipeline is introduced.

Bot discovers current Project IDs and uses `read_project_memory` for bounded, read-only source context, starting with indexes and reading selected files as needed. Project memories are not automatically injected or writable from Bot. Before creating project work, Bot verifies the Project and passes its explicit ID to `create_thread`. Current handoffs belong in Bot Notes; global memory keeps cross-project preferences. The first introduction permits orientation and memory maintenance, not implementation or configuration changes.

## Bot Notes

Agents manage one short Markdown handoff per ordinary root Thread. When Bot is enabled, working Threads record sustained work early, update meaningful progress and blockers, and reconcile the note before yielding. They remove completed or cancelled work and clear the note when no follow-up remains. A working Thread uses `read_bot_note()`, `write_bot_note(content)` and `clear_bot_note()` for its own slot. Its current note and Bot's enabled/paused status appear automatically in its model context. Bot can address ordinary root slots and list bounded summaries with `list_bot_notes`.

Open **Work notes** in Bot's supporting pane to browse Thread links, Projects, update times and Markdown previews. Expand a note to read it in full. The ordinary Thread's Notes inspector also shows its own Bot Note, separately from private working notes. Both views are read-only.

Notes hold objectives, references, findings and next follow-up, up to 16 KiB UTF-8. Empty clears; identical content changes nothing; the last committed mutation wins. Notes remain usable while Bot is off. Archive preserves them, forks start empty, and deleting a Thread removes its slot. Writing a note never wakes Bot. To request attention for a handoff, result, blocker or question, a working Thread first updates its note, then sends one substantive `send_thread_message(thread_id='thread_bot', kind='report', message=...)`. The Host retains its source Thread identity and notifies Bot's existing delivery loop. Reports remain pending while Bot is off, paused, unavailable or waiting for a human decision. Managed workers report through their Coordinator. Bot's outgoing Thread inputs include hidden return-address guidance; recipients retain their saved final result as well as reporting back.

## Messages and decisions

The footer always shows context and cost on the right. The left-side task and execution inspection controls appear in the existing **Debug** mode. Ordinary chats keep their usual footer. Context reflects the captured Run's Model capacity and latest root request tokens, not accumulated usage or your next Model selection. Missing capacity is shown as unknown. Connection feedback, actionable errors and approvals remain visible outside Debug; generic Host interruption placeholders remain available in Debug diagnostics.

Use **Voice input** when dictation is configured. Bot shares the normal chat recorder and transcription settings, including subscription account selection. Stop recording to insert the final transcript into your draft once; it does not send automatically. Attachments and voice input stay available with Debug off, including while Bot is working.

The Bot conversation shows your messages, assistant prose, final replies, and intentional proactive messages. Ordinary work logs show renderable reasoning, tool calls, results and live execution progress in the same folds as normal chats, including after refresh. Hidden injected prompts and content marked `display: false` stay hidden. For human input, Bot responds briefly before slow work and shares useful progress. Every ended visible assistant text segment marks Bot unread and sends a message notification, including intermediate prose before tools. Final settlement does not repeat the same message. While you are viewing `/bot`, Bot's in-app popups stay quiet; notifications from other conversations still appear. Unread state and system Push eligibility are unchanged. Work logs, reasoning and streaming tokens do not generate separate notifications.

Heartbeats and other automatic work use tools without a preamble or routine progress narration. Bot only reports concise important results, actionable blockers or decisions requiring your attention. Internal coordination details stay in notes and tasks. When no reply is needed, Bot calls the native `finish_silently` output tool to finish successfully without publishing a message. This does not retract prose already shown. Its call and result are hidden in live and saved conversation views, while execution history remains available for inspection. Bot can explicitly publish a verified result, blocker or question.

Answer pending human decisions directly in the Bot conversation. Bot has one chat view; notification actions and old Bot Thread links open that chat. Heartbeats and external events cannot answer an approval. The existing native panel chooser provides Files and Browser when enabled on the server; Bot does not add a separate browser implementation.

Bot uses the ordinary shared composer: trusted participants edit the same text and attachment selections with presence, synchronization and server-backed draft retention. Opening or refreshing `/bot` restores the shared draft without starting a Run. Send and Steer wait for synchronization and consume only the captured edits after acceptance; concurrent edits and rejected input remain in the draft. If a submission's response is uncertain, keep the retained command and use its explicit retry action. Retrying reuses the same input ID, ordered parts and capture; it does not create another input. Bot remains excluded from scoped guest shares. A new message starts a native Run immediately when Bot is idle. While Bot is running, it uses native immediate steering of that exact Run, including a Run started by Heartbeat or Connector input. The final text reply is visible in the conversation. A preparing, cancelling, or stale Run can reject steering; the draft remains available to send again. Rejection never queues the message or starts a replacement Run. Acceptance is not proof of completion.

## Pause and recover

Use the pause icon to hold automatic processing. Heartbeat ticks, selected Connector events and explicit Thread reports keep collecting; direct human messages remain usable. Pause does not cancel a Run or reset the Heartbeat clock. Turn off Heartbeat separately to stop ticks while allowing Connector delivery.

Turn off Bot in Settings to stop new ticks and delivery. History, settings, Notes and pending inputs are retained; already admitted work and other Threads continue. The conversation becomes read-only. Re-enable in Settings; a later normal delivery opportunity can process retained input. Pending human decisions can resume only after re-enable.

Inputs interrupted during admission or execution are retained across server restart for inspection and pause automation. Legacy queued human inputs are also marked interrupted; they are not scheduled automatically. Bot does not automatically replay these inputs: tools may already have changed files or external systems. Review the reported error and working Thread results, then send an explicit follow-up. Bot has no separate Run time limit. Provider failures and manual cancellation still use the native execution lifecycle. The obsolete `webui.bot.run_timeout_seconds` key has no effect. Existing files remain loadable under the normal additive-field policy; remove the key when updating your configuration. Missed heartbeat intervals coalesce into one opportunity instead of replaying every missed tick.

## Connector ingress foundation

The Host includes private database storage for Connector settings, credentials, and selected event envelopes. Heartbeat and selected Connector events share one automatic inbox, independent of active Model execution. A periodic delivery attempt fairly batches a pending tick with events across Connectors and submits or steers the exact Bot operation. Native acceptance consumes that batch; model acknowledgement or task completion is not required. Pending human decisions hold delivery.

Manage accounts in **Settings → Connections → Connectors**, or open a setup card offered in a conversation. [Connectors](connectors.md) supplies a trusted plugin SDK, structured setup and rule forms, private state and atomic event ingress. The built-in GitHub provider exposes service tools using the reusable Remote MCP library; that library adds no generic MCP configuration entry. Event-producing plugins own collection and matching. Connector credentials are plaintext in the private local database; protect the data directory and its backups. External event content supplies context, not human authorization.

For client integration, see the [Bot HTTP API](http-api.md#bot). For root settings, see the [configuration reference](configuration.md#webui-bot).
