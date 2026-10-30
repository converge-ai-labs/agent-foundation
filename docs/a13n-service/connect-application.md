---
title: Connect your application
description: Connect a server-side client, submit a message, read its result, and continue the conversation.
---

Connect your application's backend to an agent you have already tested in Console. You will need the Service URL, the agent ID, and a workspace API key. If you are starting from scratch, deploy Service with the [quickstart](get-started.md), then configure and test an agent with the [Console guide](use-platform.md).

## Set up credentials

1. In Console, open the agent's menu and select **Connect to app**.
2. Copy the **Service URL** and **Agent ID** into your server-side configuration.
3. Use a saved personal API key, or select **Create key**. Copy a new key before closing the dialog: its secret is shown only once.
4. Store the key as `A13N_API_KEY` in your server environment. Use **Copy for agent** if you want connection instructions for a coding assistant.

You can manage personal keys under **API keys** in the sidebar. A key acts in its workspace with the current permissions of its principal, the user or service account it belongs to. Running an existing agent requires **runner** or higher; creating one requires **builder** or **admin**. For a team application, use a dedicated [service account](identity.md#service-accounts). Keep API keys on the server, out of browser code.

## Start with an SDK

Use your language's quick start: [Python](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md), [TypeScript](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md), [Go](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md), or [Rust](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md). Each repository owns installation, method signatures, and runnable examples. Read the documentation for the version you install and match its supported Service contract to your deployment; `main` may be ahead of a released package.

Build the first interaction in this order:

1. Initialize the client with the Service URL and API key.
2. Start a conversation with the agent ID and a message such as “Summarize these project notes.” Give the submission an idempotency key. Retry the same submission with the same key if the response is lost.
3. Retain the returned conversation and submission references. Your application needs to associate the result with this message, including any time the message spends queued.
4. Read the result. Display `output` on completion, collect responses when waiting, and show the structured failure when execution fails.

Use the SDK's application workflow to follow the submission through these states. The [HTTP walkthrough below](#direct-http-integration) shows the underlying receipts and reads. A new conversation creates its session automatically, and streaming is optional when you only need the final result.

## Send a follow-up

Send the next message to the same thread to continue its history. For example, follow “Summarize these project notes” with “Which task should I do first?” Keep the thread reference and give the new submission a new idempotency key. Track its result separately from the first message.

Closing the client stops local observation; Service continues the work. Use an explicit cancellation operation when you want to stop execution. Your [SDK's application guide](sdks.md#choose-a-client) owns its streaming, cleanup, and recovery calls.

## Handle a waiting result

A waiting run needs an action before it can continue. For a question, approval, or client-side tool call:

1. Read the run's `pending` requests and show each question or decision to the responsible user, or invoke the application's client-side tool.
2. Collect and save responses in your application. Keep each response associated with its `tool_call_id` and category: `approvals` or `calls`.
3. Submit the complete batch to `POST /api/v1/runs/{run_id}/resume`, with a new idempotency key. Include both category maps, even when one is empty. Every pending request needs an explicit response; unanswered requests are not automatic approvals or denials.
4. Follow the returned successor run. The original run remains waiting as the record of that suspension.

Sending another ordinary message does not answer the wait; the message stays queued. For response shapes and desktop-control waits, follow [Waits, approvals and questions](agents-and-runs.md#waits-approvals-and-questions). For a failed run, inspect `failure` and use [troubleshooting](monitoring.md#troubleshoot-a-request-or-run).

## Direct HTTP integration

Use these steps to see the protocol before implementing a client. Run the commands in one shell with `curl`, `jq`, and `uuidgen` installed. Replace the URL and API key with your deployment's values:

```sh
export A13N_URL=http://127.0.0.1:8080
export A13N_API_KEY=a13n_replace_with_your_key
```

### Select an agent

Use the ID from Console, or list the agents available to the key:

```sh
curl --fail-with-body -sS "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

If you need to create an agent, find an enabled model's key under **Models** or with `GET /api/v1/models`. Replace `your-model-key` and send:

```sh
MODEL_KEY=your-model-key
jq -n --arg model "$MODEL_KEY" \
  '{name: "Helper", config: {model: $model, instructions: "Answer briefly."}}' > agent.json
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  --data-binary @agent.json
```

### Start a conversation

Set `AGENT_ID` to the selected agent's `id`. Prepare one message and its idempotency key:

```sh
AGENT_ID=ap_replace_with_the_returned_id
MESSAGE_KEY=$(uuidgen)
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "What is a13n?"}]}}' > message.json
```

Submit the message and save the receipt. If the response is lost, repeat this request with the same body and `MESSAGE_KEY`; do not generate a new key for that retry.

```sh
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  --data-binary @message.json > submitted.json
jq '{thread_id: .thread.id, session_id: .thread.session_id, entry_id: .entry.id, run_id: .run.id}' submitted.json
```

The receipt contains `thread`, `entry`, and `run` when the entry already has an assigned run. `run: null` means the message has been stored but has no assigned run yet. Save the IDs for later reads:

```sh
THREAD_ID=$(jq -r '.thread.id' submitted.json)
SESSION_ID=$(jq -r '.thread.session_id' submitted.json)
ENTRY_ID=$(jq -r '.entry.id' submitted.json)
```

### Read the result

Read this message's entry, including when the original receipt had `run: null`:

```sh
curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > entry.json
jq '{status, assigned_run_id, failure}' entry.json
```

| Entry status | What to do                                                                              |
| ------------ | --------------------------------------------------------------------------------------- |
| `pending`    | Read again later. If the thread is waiting, answer that wait first.                     |
| `assigned`   | Read `assigned_run_id` to observe execution, then re-read the entry when that run ends. |
| `consumed`   | The run incorporated this message into saved state. Read that run's outcome.            |
| `failed`     | Inspect the entry's `failure`; the message did not finish incorporation.                |
| `withdrawn`  | Stop waiting for an answer to this entry.                                               |

Once `assigned_run_id` is present, read the run:

```sh
RUN_ID=$(jq -r '.assigned_run_id' entry.json)
curl --fail-with-body -sS "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > run.json
jq '{status, output, pending, failure}' run.json
```

Repeat the run read while its status is `accepted` or `running`. A `completed` run has its answer in `output`; `waiting` requires the response workflow above; `failed` and `cancelled` end execution without a completed answer.

Before presenting a completed run as this message's result, re-read the entry and confirm it is `consumed` by that run. Assignment alone is not enough: a run can finish without incorporating an assigned message, returning that entry to `pending`. Continue tracking the entry in that case, rather than using the thread's latest run as its answer.

After the first answer, send a follow-up to the same thread:

```sh
MESSAGE_KEY=$(uuidgen)
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "Which component should I start with?"}]}}' > message.json
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads/$THREAD_ID/inbox" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  --data-binary @message.json > submitted.json
ENTRY_ID=$(jq -r '.entry.id' submitted.json)
```

Repeat the entry and run reads for this new `ENTRY_ID`. For live updates, follow the [session stream](agents-and-runs.md#follow-a-session-stream) using `SESSION_ID`, or subscribe to [webhooks](files-and-webhooks.md#webhooks). Keep the submission IDs so reconnecting can resume observation of the same work.

See [HTTP conventions](http.md) for pagination, conditional writes, and errors, and the [API reference](api-reference/index.md) for complete request and response schemas.
