---
title: Bot
description: Keep objectives moving with a personal assistant that coordinates working Threads and checks progress every hour.
---

Bot is the instance-wide assistant in the WebUI sidebar. Give Bot an ongoing objective. Bot can find or create Projects, start ordinary working Threads, and inspect their saved results on later heartbeats. Its conversation uses the existing Thread reader, editor, and native panels.

## Start and configure Bot

1. Open **Settings → General → Bot**. Select an existing Model. Bot uses built-in instructions; you do not select an Agent.
2. Optionally add custom instructions, such as your preferred coordination style. Captured global `AGENTS.md` guidance also applies.
3. Open **Bot** in the sidebar and send a message. The first accepted message activates heartbeat scheduling.

The default heartbeat interval is **one hour**. Opening the page or saving settings starts no model request. The WebUI server must remain running for heartbeat processing. Bot is shared by instance-key participants and is unavailable to scoped guests. [Live mode](live-mode.md) is independent.

Merge these fields into the selected root YAML's existing `webui` mapping:

```yaml title="a13n-harness-ui.yaml — Bot"
webui:
  bot:
    model: model-coordinator
    instructions: "Prefer coordinating work in project Threads."
    heartbeat_enabled: true
    interval_seconds: 3600
    connector_interval_seconds: 60
    run_timeout_seconds: 180
```

Use an existing Model ID. Null or omitted `model` follows only the Model of the global default Agent. Its Agent instructions, children, Plugins, and MCP selections are not inherited. A missing effective Model prevents execution. Model and instruction changes apply to later Runs.

Heartbeat intervals accept 60–86,400 seconds. Connector delivery intervals accept 1–86,400 seconds. Run timeouts accept 10–3,600 seconds. Custom instructions accept up to 1,048,576 characters. Validate file edits with `a13n-harness-ui config validate`.

## Coordinate work and memory

Bot has an exclusive `bot/` workspace beside the selected root YAML. With the default configuration, this is `~/.a13n-harness-ui/bot/`. An explicit `--config` changes that parent directory. Bot's built-in instructions recommend using this folder for coordination artifacts and driving ordinary working Threads for substantial work.

Bot can use `create_project` to create a normal Project configuration and `create_thread` to start work with normal Sidekick/global Agent defaults. Project creation, Thread creation, Run admission, and task completion are separate outcomes. Bot is instructed to inspect saved results before reporting success.

When memory is enabled, Bot receives global memory normally. It can discover configured Projects and call `read_project_memory` to read a Project's memory on demand. These reads are bounded and read-only. Project memories are not automatically injected into Bot's context. Keep coordination notes and tasks in Bot's own Thread.

## Messages, activity, and decisions

The Bot conversation shows your messages, direct replies, and intentional proactive messages. Routine heartbeat output stays quiet. When no reply is needed, Bot calls the native `no_op` output tool to finish successfully without a message. Its call and result are hidden in live and saved conversation views, while execution history remains available for inspection. Bot can explicitly publish a verified result, blocker, or question; publication participates in existing unread and notification behavior.

Use the **Activity** icon to inspect the ordinary Bot Thread history and answer pending human decisions. Heartbeats and external events cannot answer an approval. The existing native panel chooser provides Files and Browser when enabled on the server; Bot does not add a separate browser implementation.

The text draft stays in the current tab. Reloading can lose an unsent draft. If a submission's response is uncertain, keep the retained command and use its explicit retry action. Retrying reuses the same input ID and text; it does not create another input. An accepted input is queued work, not proof that a Run started or finished.

## Pause and recover

Use the pause icon to stop automated heartbeat and Connector delivery. Direct messages remain usable. Pausing does not cancel a Run already in progress. Resume schedules the next heartbeat from the current time.

Queued inputs survive server restart. An input interrupted during admission or execution is retained for inspection and pauses automation. Bot does not automatically replay it: tools may already have changed files or external systems. Inspect Activity and working Thread results, then send an explicit follow-up. A Run timeout uses the same pause-and-inspect behavior. Missed heartbeat intervals coalesce into one opportunity instead of replaying every missed tick.

## Connector ingress foundation

The Host includes private database storage for Connector settings, credentials, and selected event envelopes. A periodic delivery attempt batches pending events across Connectors and submits or steers the exact Bot operation. Native acceptance consumes that batch; model acknowledgement or task completion is not required. Pending human decisions hold delivery.

This release provides the common storage and delivery foundation. Concrete provider setup, registrations, polling, write tools, and a Connector management UI require provider-specific integration. Connector credentials are plaintext in the private local database; protect the data directory and its backups. External event content supplies context, not human authorization.

For client integration, see the [Bot HTTP API](http-api.md#bot). For root settings, see the [configuration reference](configuration.md#webui-bot).
