---
title: Live Mode
description: Talk with your agents through a continuous voice conversation.
---

Live Mode gives the WebUI a continuous voice conversation with a persistent place for tasks and decisions. Start once, speak naturally, and move around the workspace while the microphone remains on. Stop voice separately from work already running.

## Enable Live

1. Add a Model with **Codex subscription** authentication and sign in to the selected account. See [Models and authentication](models-and-authentication.md).
2. Open **Settings → General → Live**. Enable Live and select that Model.
3. Choose the voice and whether the Live executor can use file tools. Save the settings.
4. Press **Start voice** in the workspace header or on the **Live** page. Allow microphone access when the browser asks.

The workspace Start voice control and sidebar Live link appear only after Live is enabled. Enable it from Settings; the workspace does not show a setup prompt.

Use HTTPS or localhost in a browser that supports microphone capture and WebRTC. The selected account and network must support Codex Live. A successful ordinary Model request does not verify voice availability.

Setup and adding a Codex Model offer an initially checked Live option. Clear it to leave Live unconfigured. Existing Live settings are preserved. Upgrades do not turn Live on automatically.

The equivalent root configuration is:

```yaml title="a13n-harness-ui.yaml"
live:
  enabled: true
  model: model-codex
  voice: cove
  files_enabled: true
  instructions: "Keep spoken replies concise."
```

Live uses Codex V3 voices and defaults to `cove`. V2 voices such as `marin` and `cedar` are incompatible; replace them in any earlier Live configuration.

Use an existing Codex subscription Model ID. Live needs no additional Model or Agent YAML file. Its speech model is a runtime preset; the selected Model performs delegated tasks.

## Talk and Work

Opening the Live page leaves the microphone off. Press **Start voice** to begin a continuous conversation. The workspace header starts the same conversation without leaving your current page. Press **Stop voice** on the Live page or **Stop** in the header to release the microphone and connection.

Use **Open tasks & decisions** to inspect the persistent Live Thread. Tasks run through the ordinary Harness executor. Decisions and approvals remain in that Thread. Live can coordinate independent roots and Coordinators; managed workers retain their existing ownership rules.

The built-in executor has notes, tasks, Thread collaboration, enabled global memory, and optional file tools. It uses `live-workspace` beside the selected configuration file. It does not inherit your default Agent's shell, MCP servers, plugins, Skills, or subagents. Delegate specialized work to a configured Thread instead.

Navigation keeps the same voice connection. A compact **Microphone on** indicator and **Stop** control remain visible in the workspace. Select the indicator to open the full Live page. That page shows **Connected** and recent captions labeled **You** or **Assistant**; stopping hides unfinished captions. A full reload or closed tab ends voice; returning never starts the microphone automatically. Only one browser can own voice in the App at a time.

## Results and Reports

A delegated task starts or steers the Live Thread. The selected final result returns to voice while the call is active. Stopping voice leaves the task running, and its saved outcome remains available in the Thread.

Other independent roots and Coordinators can use `send_thread_message(thread_id="live", message="...", kind="report")` to report directly to the connected voice conversation. Use `kind="task"` when the message needs further executor work. Offline delivery returns `skipped` with reason `live_offline`. It creates no new Run and is not queued for later playback.

Speech transcripts and task history persist independently. Context accepted by the provider is not confirmation that the user heard it. Check the source Thread for a task's saved result.

## Privacy and Connection Errors

Audio travels directly between the browser and OpenAI. Subscription credentials remain on the Host. The Host handles signaling, captions, delegation, and selected result text; it does not relay microphone audio.

If the connection fails, read the error and start a new session after correcting microphone, network, or account access. Provider HTTP errors are shown without exposing response bodies or credentials. Live does not silently retry a task, replace the call, change accounts, or fall back to an API key. Resolve pending decisions before adding more work. Restart voice after changing its settings.
