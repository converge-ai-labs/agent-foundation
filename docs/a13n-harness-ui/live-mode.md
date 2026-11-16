---
title: Live Mode
description: Enable voice, try a first conversation, and follow delegated work in the Live Thread.
---

Live Mode lets you speak to Harness UI while moving between WebUI pages. Voice and task execution have separate lifetimes: stopping the microphone leaves already-started tasks running. Their results and decisions stay in the persistent Live Thread.

## Enable Live

Before starting, configure and sign in to a [Codex subscription Model](models-and-authentication.md). Open WebUI over HTTPS or localhost in a browser with microphone capture and WebRTC support. Voice also requires account and network access to Codex Live.

1. Open **Settings → Chats → Live**.
2. Enable Live and select your existing Codex subscription Model.
3. Choose a voice. Keep the default `cove` for the first test. Choose whether the Live executor may use file tools, then save.
4. Select **Open Live** in the same section.
5. Press **Start voice** and allow microphone access. Wait for **Connected**.
6. Say a short request, such as “Tell me what tasks you can help coordinate.” Check the **You** and **Assistant** captions and the spoken response.
7. Press **Stop** in the header or **Stop voice** on the Live page. Confirm that the microphone indicator clears.

For file-based configuration, run `a13n-harness-ui config path` to locate `configuration_path`. Merge this fragment into that root YAML, replacing `model-codex` with the saved Model ID:

```yaml title="a13n-harness-ui.yaml — merge into the root"
live:
  enabled: true
  model: model-codex
  voice: cove
  files_enabled: true
  instructions: "Keep spoken replies concise."
```

Run `a13n-harness-ui config validate` against the same configuration root, then follow steps 4–7. A valid configuration or successful ordinary Model request does not establish voice connectivity; the first call checks that path. Restart voice after changing Live settings.

The selected Model supplies authentication and executes delegated tasks. Speech uses a runtime preset, so you do not create a separate Live Model or Agent file. Live accepts Codex V3 voices; replace legacy `marin` or `cedar` settings before starting a call.

First-use WebUI setup offers a checked Live option for Codex. Clear it if you do not want Live. Adding a Model later leaves Live settings unchanged, as does upgrading. Existing explicit settings, including disabled settings, are preserved.

## Talk and Work

Try a small delegated task after the voice test:

1. Start voice and ask Live to record a task or coordinate work with a configured Thread.
2. Select **Open tasks & decisions**. Inspect the Live Thread to see the executor's work and answer any pending decision.
3. Return to another WebUI page. The **Microphone on** indicator remains visible while the same call continues.
4. Open the saved task result when execution finishes. If you stop voice first, return to the Live Thread to follow the work; use its ordinary cancellation control if you also want the task stopped.

The built-in Live executor has notes, tasks, Thread collaboration, enabled global memory, and optional file tools. Its workspace is `live-workspace` beside the selected configuration file. It has its own restricted tool set rather than inheriting the default Agent's shell, MCP, plugins, Skills, CodeAct, or subagents. Delegate specialized work to a configured Thread. Managed workers retain their existing Coordinator ownership.

Opening the Live page leaves the microphone off. Navigation inside WebUI keeps the active call; a full reload, closed tab, or Stop ends it. Returning requires another Start action. One browser can own voice in an App at a time. Clicking the microphone indicator opens the full Live page and recent captions; stopping hides unfinished captions.

## Results and Reports

A spoken delegation starts the Live executor, or steers compatible active work. Pending decisions remain in its Thread. During an active call, the selected successful final result is returned to voice after saving; tool chatter and private reasoning are not automatically spoken.

Other independent roots and Coordinators can send a message to the persistent Live executor. This is an Agent tool call, not a shell command:

```python
send_message(to="live", message="The requested checks passed.")
```

Messages persist even without a voice call and can run an eligible executor offline. Preparing work, unavailable settings and pending decisions hold input. The executor decides how to respond; messages have no `kind` parameter or direct speech path. The receipt `{accepted: true, input_id}` means persisted, not read or processed. Only a currently active call receives its saved response. Offline results stay in Thread history, and reconnecting never speaks old results. Messages do not start the microphone. Managed workers and children gain no additional destination authority.

Speech transcripts and task history are saved separately. Accepted speech context does not prove that the user heard it. Open the source Thread for the task's saved result, especially after a disconnect.

## Privacy and Connection Errors

The browser sends audio directly to OpenAI. Subscription credentials stay on the Host, which handles signaling, captions, delegation, and selected result text without relaying microphone audio.

| Problem                                   | What to do                                                             |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| No Live link or Start control             | Enable Live in Chat settings and save.                                 |
| Microphone cannot start                   | Check browser permission, HTTPS or localhost, and capture support.     |
| Another browser owns voice                | Stop that call before starting here.                                   |
| Provider or network connection fails      | Read the error, correct access or connectivity, then start a new call. |
| Task needs a decision                     | Open tasks & decisions and resolve it in the Live Thread.              |
| Task finished but no voice result arrived | Read the saved result in its Thread.                                   |

Connection failure ends that call. Live does not replay tasks, switch accounts, or fall back to an API key. Provider errors omit response bodies and credentials. Check the existing task state before submitting the same work again.
