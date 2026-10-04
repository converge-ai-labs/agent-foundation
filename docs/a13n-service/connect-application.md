---
title: Connect your application
description: Call an agent on a running Service from your application with a workspace API key.
---

You need the Service URL, a workspace API key, and an agent with a working model. Start with the [Service quickstart](get-started.md) for deployment or [Console guide](use-platform.md) to configure an agent.

Use a [Service SDK or the remote CLI](sdks.md) for application integration. This guide shows the equivalent HTTP flow with curl; client-specific setup and streaming helpers live in the SDK repositories.

## Set up credentials

Create an API key under **Workspace settings → My API keys** and export it with the Service URL:

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

The key acts in its workspace with the current permissions of its principal: the user or service account it belongs to. You need the **runner** role or higher in that workspace; creating agents requires **builder** or **admin**. For a team application, use a dedicated [service account](identity.md#service-accounts). Keep API keys in server-side configuration.

## Select an agent

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

## Start a conversation

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

## Read the result

The response contains `thread`, `entry`, and possibly `run`. If `run` is null, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) to observe acceptance. Otherwise, set `RUN_ID` to the returned run's `id` and read its status:

```sh
RUN_ID=run_replace_with_the_returned_id
curl "$A13N_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

Repeat this request until the run is `completed`, `waiting`, `failed`, or `cancelled`. A completed run's answer is in `output`. A waiting run needs an [answer, approval, or client tool result](agents-and-runs.md#waits-approvals-and-questions); inspect the result of a failed run with the [troubleshooting guide](monitoring.md#troubleshoot-a-request-or-run).

Instead of polling, follow the thread stream or subscribe to [webhooks](files-and-webhooks.md#webhooks). The run continues when the client disconnects. Send follow-ups to `POST …/threads/{thread_id}/inbox` to continue the thread.

See [HTTP conventions](http.md) for authentication, pagination, conditional writes, and errors; the [API reference](api-reference/index.md) lists request and response schemas. Match your [SDK's supported contract](sdks.md#keep-version-ownership-clear) to your deployed Service version.
