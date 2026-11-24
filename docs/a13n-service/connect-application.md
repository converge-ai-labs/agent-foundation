---
title: Connect your application
description: Integrate managed Service agents into your backend using workspace API keys, official SDKs, or the HTTP API with idempotency keys and human approval resume.
---

Connect your backend to a tested Service agent using the **Service URL**, **Agent ID**, and a workspace **API key** (`runner` or higher; use a [service account](identity.md#service-accounts) for production apps and keep keys server-side).

## Set up credentials

Open the agent's menu in Console and click **Connect to app**, copy the **Service URL** and **Agent ID**, create or select a workspace API key, and store it as `A13N_API_KEY` on your server.

## Start with an SDK

Run with Python 3.13 (`uv run --python 3.13 --with 'a13n==0.0.98' call_agent.py`; see [SDK guides](sdks.md#choose-a-client) for TypeScript, Go, and Rust):

```python title="call_agent.py"
import asyncio
import os
from a13n import Client


async def main() -> None:
    async with Client(os.environ["A13N_URL"], os.environ["A13N_API_KEY"]) as client:
        agent = client.agents(os.environ["AGENT_ID"])
        message = await agent.start("Name two colors.")
        result = await message.result()
        print(result.output if result.status == "completed" else result.status)


asyncio.run(main())
```

Retain `message.thread.id` to send follow-ups to the same conversation or inspect it in Console (**Sessions**).

## Handle a waiting result

When `result.status == "waiting"`, inspect `pending.approvals` and `pending.calls`, collect user decisions or execute client-side tools, and submit **both** maps to `POST /api/v1/runs/{run_id}/resume` with a fresh `Idempotency-Key`, then follow the returned successor run (see [Waits, approvals and questions](agents-and-runs.md#waits-approvals-and-questions)).

## Direct HTTP integration

Submit a message with a unique `Idempotency-Key` (reuse the exact key and body if retrying a lost response), then poll the inbox entry and assigned run:

```sh
export A13N_URL=http://127.0.0.1:8080
export A13N_API_KEY=a13n_replace_with_your_key
AGENT_ID=ap_replace_with_your_agent_id
MESSAGE_KEY=$(uuidgen)

curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Name two colors.\"}]}}" \
  > submitted.json

THREAD_ID=$(jq -r '.thread.id' submitted.json)
SESSION_ID=$(jq -r '.thread.session_id' submitted.json)
ENTRY_ID=$(jq -r '.entry.id' submitted.json)

curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" > entry.json
RUN_ID=$(jq -r '.assigned_run_id' entry.json)

curl --fail-with-body -sS "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq '{status, output, pending, failure}'
```

Confirm the inbox entry is `consumed` by that `RUN_ID`, and send follow-ups to `POST /api/v1/threads/$THREAD_ID/inbox` with a new `Idempotency-Key`. For live events instead of polling, subscribe to [session SSE streams](stream-and-read-results.md#follow-a-session-stream) (`$SESSION_ID`) or [webhooks](webhooks.md).
