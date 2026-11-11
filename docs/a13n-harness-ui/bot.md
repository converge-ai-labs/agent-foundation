---
title: Bot
description: Keep objectives moving with a personal assistant that coordinates working Threads and checks progress every hour.
---

Bot is the instance-wide assistant in the WebUI sidebar. Give Bot an ongoing objective. Bot can find or create Projects, start ordinary working Threads, and inspect their saved results on later heartbeats. Its conversation uses the existing Thread reader, editor, and native panels.

## Start and configure Bot

1. Open **Bot** in the sidebar. Bot is off by default. Select an existing Model in its inline setup; Bot uses built-in instructions, not a configured Agent.
2. Optionally add custom instructions, such as your preferred coordination style. Custom instructions are appended alongside the built-in instructions and do not replace them. Captured global `AGENTS.md` guidance also applies.
3. Choose **Turn on Bot**. This saves settings without running the Model. The first Heartbeat is scheduled one interval later, even if you never send a message. Edit later in **Settings → General → Bot**.

The default heartbeat interval is **one hour**. Opening the page or saving settings starts no model request. The WebUI server must remain running for heartbeat processing. Bot is shared by instance-key participants and is unavailable to scoped guests. [Live mode](live-mode.md) is independent.

Merge these fields into the selected root YAML's existing `webui` mapping:

```yaml title="a13n-harness-ui.yaml — Bot"
webui:
  bot:
    enabled: true
    model: model-coordinator
    instructions: "Prefer coordinating work in project Threads."
    heartbeat_enabled: true
    interval_seconds: 3600
    connector_interval_seconds: 60
```

Use an existing Model ID. Null or omitted `model` follows only the Model of the global default Agent. Its Agent instructions, children, Plugins, and MCP selections are not inherited. A missing effective Model prevents execution. Model and instruction changes apply to later Runs.

Heartbeat intervals accept 60–86,400 seconds. Connector delivery intervals accept 1–86,400 seconds. Custom instructions accept up to 1,048,576 characters. Validate file edits with `a13n-harness-ui config validate`.

## Coordinate work and memory

Bot has an exclusive `bot/` workspace beside the selected root YAML. With the default configuration, this is `~/.a13n-harness-ui/bot/`. An explicit `--config` changes that parent directory. Bot has native file tools to read and maintain coordination artifacts. Its built-in Environment capability enables files and leaves Shell and Computer tools disabled. Bot's built-in instructions recommend using this folder for coordination artifacts and driving ordinary working Threads for substantial work.

Keep always-applicable workspace guidance in `bot/AGENTS.md`. When present, its contents are automatically included as bounded file context on every Bot Run, alongside global `AGENTS.md` guidance.

Bot manages recurring checks in `bot/HEARTBEAT.md`. Each heartbeat tells Bot to read the current file if it exists and follow its checklist. The contents are read on demand rather than automatically injected. Bot can create and edit the file to track checks, priorities, and follow-up criteria, and remove obsolete items. You can edit it too. A missing file does not block a heartbeat. File edits change future checks without triggering a Run; heartbeat timing still comes from Settings.

Bot can use `create_project` to create a normal Project configuration and `create_thread` to start work with normal Sidekick/global Agent defaults. Project creation, Thread creation, Run admission, and task completion are separate outcomes. Bot is instructed to inspect saved results before reporting success.

When memory is enabled, Bot receives global memory normally. It can discover configured Projects and call `read_project_memory` to read a Project's memory on demand. These reads are bounded and read-only. Project memories are not automatically injected into Bot's context. Keep coordination notes and tasks in Bot's own Thread.

## Bot Notes

Agents manage one short Markdown handoff per ordinary root Thread. A working Thread uses `read_bot_note()`, `write_bot_note(content)` and `clear_bot_note()` for its own slot. Bot can address any ordinary root's slot and list bounded summaries with `list_bot_notes`. There is no human Notes management form.

Notes hold objectives, references, findings and next follow-up, up to 16 KiB UTF-8. Empty clears; identical content changes nothing; the last committed mutation wins. Notes remain usable while Bot is off. Archive preserves them, forks start empty, and deleting a Thread removes its slot. Writing a note never wakes Bot. On the next legitimate input, Bot can inspect saved results, follow up, update notes and clear handled items.

## Messages and decisions

The Bot conversation shows your messages, all ordinary final text replies, and intentional proactive messages. The same rule applies to human, Heartbeat and Connector inputs, whether they start or steer a Run. Tool calls and intermediate text stay out of this conversation. When no reply is needed, Bot calls the native `finish_silently` output tool to finish successfully without a message. Its call and result are hidden in live and saved conversation views, while execution history remains available for inspection. Bot can explicitly publish a verified result, blocker, or question; publication participates in existing unread and notification behavior.

Answer pending human decisions directly in the Bot conversation. Bot has one chat view; notification actions and old Bot Thread links open that chat. Heartbeats and external events cannot answer an approval. The existing native panel chooser provides Files and Browser when enabled on the server; Bot does not add a separate browser implementation.

The text draft stays in the current tab. Reloading can lose an unsent draft. If a submission's response is uncertain, keep the retained command and use its explicit retry action. Retrying reuses the same input ID and text; it does not create another input. A new message starts a native Run immediately when Bot is idle. While Bot is running, it uses native immediate steering of that exact Run, including a Run started by Heartbeat or Connector input. The final text reply is visible in the conversation. A preparing, cancelling, or stale Run can reject steering; the draft remains available to send again. Rejection never queues the message or starts a replacement Run. Acceptance is not proof of completion.

## Pause and recover

Use the pause icon to hold automatic processing. Heartbeat ticks and selected Connector events keep collecting; direct messages remain usable. Pause does not cancel a Run or reset the Heartbeat clock. Turn off Heartbeat separately to stop ticks while allowing Connector delivery.

Turn off Bot in Settings to stop new ticks and delivery. History, settings, Notes and pending inputs are retained; already admitted work and other Threads continue. The conversation becomes read-only. Re-enable in Settings; a later normal delivery opportunity can process retained input. Pending human decisions can resume only after re-enable.

Inputs interrupted during admission or execution are retained across server restart for inspection and pause automation. Legacy queued human inputs are also marked interrupted; they are not scheduled automatically. Bot does not automatically replay these inputs: tools may already have changed files or external systems. Review the reported error and working Thread results, then send an explicit follow-up. Bot has no separate Run time limit. Provider failures and manual cancellation still use the native execution lifecycle. The obsolete `webui.bot.run_timeout_seconds` key has no effect. Existing files remain loadable under the normal additive-field policy; remove the key when updating your configuration. Missed heartbeat intervals coalesce into one opportunity instead of replaying every missed tick.

## Connector ingress foundation

The Host includes private database storage for Connector settings, credentials, and selected event envelopes. Heartbeat and selected Connector events share one automatic inbox, independent of active Model execution. A periodic delivery attempt fairly batches a pending tick with events across Connectors and submits or steers the exact Bot operation. Native acceptance consumes that batch; model acknowledgement or task completion is not required. Pending human decisions hold delivery.

This release provides the common storage and delivery foundation. Concrete provider setup, polling, write tools, and a Connector management UI require provider-specific integration. Connector credentials are plaintext in the private local database; protect the data directory and its backups. External event content supplies context, not human authorization.

For client integration, see the [Bot HTTP API](http-api.md#bot). For root settings, see the [configuration reference](configuration.md#webui-bot).
