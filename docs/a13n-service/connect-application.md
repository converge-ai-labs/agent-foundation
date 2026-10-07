---
title: Connect your application
description: Start a conversation, read the answer, and send a follow-up through a Service SDK.
---

You need the Service URL, a workspace API key, and an agent with a working model. Start with the [Service quickstart](get-started.md) for deployment or [Console guide](use-platform.md) to configure an agent.

## Set up credentials

Create an API key under **Workspace settings → My API keys**. Keep the key and Service URL in server-side configuration. The key acts in its workspace with the current permissions of its principal: the user or service account it belongs to. You need the **runner** role or higher in that workspace; creating agents requires **builder** or **admin**. For a team application, use a dedicated [service account](identity.md#service-accounts).

## Start with an SDK

Choose your language's quick start: [Python](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md), [TypeScript](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md), [Go](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md), or [Rust](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md). Each repository owns installation and runnable examples. Match its supported Service contract to your deployment and read the documentation at the version you install; `main` may be ahead of a released package.

1. Initialize the client with your Service URL and API key.
2. Select an existing agent by ID. Find the ID in Console, or create an agent with the [Console guide](use-platform.md#create-your-own-agent).
3. Start the agent with your first message, such as “Summarize these project notes.” Give this submission an idempotency key; reuse it if you retry the same message after losing the response.
4. Wait for the result and handle its status. A completed result contains the answer; waiting, failed, and cancelled are distinct outcomes.

The Python high-level workflow uses `start` and `result`; follow its [quick start](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md) for the exact calls and cleanup. Other SDKs follow their language's conventions. You do not need to create a session separately or poll individual runs. The SDK tracks when your input is consumed, including time spent queued. Streaming is optional when you only need the final result.

## Send a follow-up

Keep the returned thread reference and send your next message to that thread using the SDK's high-level send operation. For example, follow “Summarize these project notes” with “Which task should I do first?” Give the new message a new idempotency key. The existing history remains available.

Closing the client or disconnecting stops local observation; it does not stop the agent's work. Stopping execution is a separate explicit action. See your SDK's [application guide](sdks.md#choose-a-client) for streaming, cleanup, and recovery.

## Handle a waiting result

If the agent asks a question, needs approval, or calls a client-side tool, the result is waiting. Show the pending requests, collect explicit answers, and use the SDK's waiting-action workflow to submit them. The `/resume` endpoint requires a complete response batch; `/answers` saves one answer at a time and resumes when all are collected. Do not fill unanswered requests with automatic approvals or denials. Resuming continues the work in a successor run.

For failures, inspect the structured failure and follow [troubleshooting](monitoring.md#troubleshoot-a-request-or-run). See [Waits, approvals and questions](agents-and-runs.md#waits-approvals-and-questions) when you need the underlying protocol.

## Direct HTTP integration

Use this path when implementing a protocol client or investigating a request. The [SDKs and remote CLI](sdks.md) provide the ordinary application and scripting paths.

Export the same Service URL and workspace API key:

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

### Select an agent

Find the agent's ID in Console or list the agents available to your key:

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

To create one, find a model key under **Models** or with `GET /api/v1/models`, then replace `your-model-key` below:

```sh
MODEL_KEY=your-model-key
curl -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d "{\"name\": \"Helper\", \"config\": {\"model\": \"$MODEL_KEY\", \"instructions\": \"Answer briefly.\"}}"
```

### Start a conversation

Set `AGENT_ID` to the selected agent's `id` and generate an idempotency key for this message:

```sh
AGENT_ID=ap_replace_with_the_returned_id
MESSAGE_KEY=$(uuidgen)
```

Submit the first message. To retry after a lost response, repeat this request with the same `MESSAGE_KEY`. Use a new key for a different message.

```sh
curl -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\": \"$AGENT_ID\", \"payload\": {\"content\": [{\"type\": \"text\", \"text\": \"What is a13n?\"}]}}"
```

### Read the result

The response contains `thread`, `entry`, and possibly `run`. If `run` is null, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) to observe acceptance. Otherwise, set `RUN_ID` to the returned run's `id` and read its status:

```sh
RUN_ID=run_replace_with_the_returned_id
curl "$A13N_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

Repeat this request until the run is `completed`, `waiting`, `failed`, or `cancelled`. A completed run's answer is in `output`. A waiting run needs an [answer, approval, or client tool result](agents-and-runs.md#waits-approvals-and-questions); inspect the result of a failed run with the [troubleshooting guide](monitoring.md#troubleshoot-a-request-or-run).

Instead of polling, follow the thread stream or subscribe to [webhooks](files-and-webhooks.md#webhooks). The run continues when the client disconnects. Send follow-ups to `POST …/threads/{thread_id}/inbox` to continue the thread.

See [HTTP conventions](http.md) for authentication, pagination, conditional writes, and errors; the [API reference](api-reference/index.md) lists request and response schemas. Match your [SDK's supported contract](sdks.md#keep-version-ownership-clear) to your deployed Service version.
