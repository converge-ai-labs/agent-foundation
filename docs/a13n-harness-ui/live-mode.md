---
title: Live Mode
description: Engage in real-time full-duplex voice conversations in WebUI while coordinating background tasks across your codebase.
---

**Live Mode** allows you to talk naturally with an agent via bidirectional WebRTC audio while freely navigating the Harness UI WebUI interface. Voice interaction and task execution operate on decoupled lifecycles: closing the voice channel keeps background coding tasks running, and their results are cleanly preserved in the persistent Live Thread.

## Enable Live

To use Live Mode, configure a supported real-time voice model (such as an OpenAI real-time model) and access WebUI over HTTPS or `localhost` with microphone permissions:

1. In WebUI, navigate to **Settings → Chats → Live**.
2. Toggle Live Mode to **Enabled** and select your configured voice model.
3. Choose a voice preset (such as `cove` or `alloy`). Optionally enable file tools if you want the voice coordinator to directly inspect workspace paths.
4. Click **Open Live** to enter the voice dashboard.
5. Press **Start voice** and grant browser microphone permission. When status transitions to **Connected**, you can begin speaking.
6. Speak a short task, e.g., *"Summarize the latest commits on the current branch."* Review the live transcript captions and listen to the spoken reply.
7. Click **Stop voice** in the header or on the Live page whenever you wish to pause audio capture.

You can also configure Live Mode declaratively in your `a13n-harness-ui.yaml` configuration file:

```yaml title="a13n-harness-ui.yaml"
live:
  enabled: true
  model: model-openai-realtime
  voice: cove
  files_enabled: true
  instructions: "Keep spoken answers concise, informative, and natural."
```

Validate your setup with `a13n-harness-ui config validate`.

## Talk and Work

Live Mode lets you speak conversationally while delegating complex tasks to dedicated background worker threads:

1. Start voice and ask the agent to perform an engineering task, such as refactoring a module or writing unit tests.
2. The voice coordinator delegates the execution to a specialized background worker thread.
3. Open **Tasks & decisions** to watch the worker's execution progress, inspect file modifications, and answer any tool approval requests.
4. Continue browsing other WebUI tabs or reviewing Git diffs. The **Microphone on** indicator in the header confirms that your audio session remains active.
5. When the background job finishes, return to its dedicated thread to review the final changes.

This decoupled execution keeps your voice conversation responsive while long builds or test suites run in the background.

## Results and Reports

Spoken instructions can either be handled immediately by the voice executor or dispatched as structured background tasks.

Worker threads and coordinator agents can send status reports back to the active voice stream using the inter-thread messaging tool:

```python
send_thread_message(
    thread_id="live",
    message="Unit tests completed successfully with zero regressions.",
    kind="report",
)
```

- Use `kind="report"` when you want the voice coordinator to synthesize and speak the completion update to the user.
- Use `kind="task"` when the coordinator needs to plan further follow-up actions.

Speech transcripts and task history are stored independently, ensuring full traceability across sessions.

## Privacy and Connection Errors

Voice audio is transmitted directly over encrypted WebRTC channels to the model provider. Sensitive workspace files and database credentials stay safely on your host machine.

| Common issue                            | Troubleshooting step                                                                                        |
| :-------------------------------------- | :---------------------------------------------------------------------------------------------------------- |
| **Start voice button disabled**         | Verify that Live is enabled in Settings and a valid real-time model is selected                             |
| **Microphone permission blocked**       | Ensure the browser is granted microphone access and WebUI is loaded via `localhost` or HTTPS                |
| **Connection dropped**                  | Check network access to the provider's WebRTC endpoints and retry connecting                                |
| **Task requires approval**              | Open the **Tasks & decisions** panel in WebUI to confirm any pending tool executions                        |
| **Audio disconnected during long task** | The background task continues running in its thread; review final results directly in the WebUI thread view |
