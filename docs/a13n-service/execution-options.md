---
title: Advanced execution options
description: Import history, forward business context, and configure tool handoff and steering.
---

Use these options when your application needs imported history, business context headers, or control over tool handoff and steering. Start with a working [application integration](connect-application.md); preserve the accepted policy and inspect uncertain tool outcomes before retrying.

## Import conversation context

To continue an external conversation, put completed history beside the new message in `POST /threads`. Replace the agent placeholder:

```json
{
  "agent_id": "ap_...",
  "message_history": [
    {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "We are planning a trip to Kyoto."}]},
    {"kind": "response", "parts": [{"part_kind": "text", "content": "How many days will you stay?"}]}
  ],
  "payload": {"content": [{"type": "text", "text": "Three days. Suggest an itinerary."}]}
}
```

`message_history` uses Pydantic AI message JSON, capped at 256 messages and 256 KiB of normalized JSON. It accepts user text, model text and closed tool-call/result exchanges. Every historical call needs a matching return by name and ID before the next response, user message or end of the import. Historical tools are context and do not execute.

Put instructions in the agent config and media in the current payload. Imports accept completed conversation content, not system instructions or suspended execution. Foreign application metadata and run/conversation IDs remain readable in the submitted JSON but are cleared before execution; imported usage does not become Service accounting.

The import initializes only this new thread. Follow-ups and forks continue its committed checkpoint. It creates no historical Service runs or display items, and existing thread history cannot be replaced. Python applications already using Pydantic AI can serialize with `ModelMessagesTypeAdapter.dump_json`; other clients can supply the JSON directly.

## Request context headers

Pass business metadata shared by model and MCP requests through the message body:

```json
{"options": {"context": {"headers": {"x-trace-id": "trace-123", "x-tenant-id": "acme"}}}}
```

Forwarding is enabled by default. Include only values intended for those providers and MCP servers. Values are readable run options; store secrets in provider or connection credentials. Inbound HTTP headers are not copied automatically. The map allows 32 case-insensitively unique names, printable ASCII values up to 4096 characters, and 8 KiB overall; authentication and transport names are reserved.

Context persists by default within a Thread. Later messages that omit it retain the latest accepted value; an explicit object replaces the entire context, and `{"options": {"context": {"headers": {}}}}` clears it. New runs awakened by delayed tool or child results use the Thread's latest value, not the originating run's old value. An active run and its recovery attempts keep their admitted snapshot. A steer can omit or repeat context; a different value queues for a later run. Snapshot Threads use the same context lifecycle while retaining their frozen execution configuration.

Async children initially inherit the parent run's snapshot, then keep their own Thread-local context. An ordinary fork starts empty when context is omitted; a retained-snapshot fork inherits its source context. HTTP reads expose context, while MCP query projections remove it. Prompts, history and command environments receive no automatic copy.

Model request headers and per-connection `mcp_headers` override shared names; credentials and protected protocol headers remain authoritative. Forwarding requires both the deployment category switch and the target's `config.forward_context_headers`. Omitted target settings use `A13N_HARNESS_FORWARD_CONTEXT_HEADERS` (default true); MCP saves store the resolved value. Disable a category with `A13N_PROVIDERS__FORWARD_MODEL_CONTEXT_HEADERS=false` or `A13N_PROVIDERS__FORWARD_MCP_CONTEXT_HEADERS=false`. Fetch, browser and shell operations do not automatically forward these headers.

## Caller headers

Use thread-level `mcp_headers` when only a particular MCP connection needs non-secret context. Set it on thread creation or PATCH the thread with `If-Match`:

```json
{"mcp_headers": {"conn_...": {"x-tenant-id": "acme"}}}
```

Replace the placeholder with an enabled workspace MCP connection ID. The map permits 32 connections and 16 KiB total. Authentication and protocol names, including names used by the connection's own credentials, are refused. Each run freezes these headers at acceptance; forks and async children inherit them. Run views omit their values.

## Execution policy

Set tool handoff and steering independently:

- `tools.mode: inline` waits for tool completion; `auto` can return a retained execution receipt after the foreground wait.
- `steering: boundary` applies guidance at a model boundary; `immediate` can preempt a foreground wait.

Service defaults to `auto`, a five-second foreground wait and `immediate`. This agent-config fragment makes those choices explicit:

```json
{"execution": {"tools": {"mode": "auto", "foreground_wait_seconds": 5}, "steering": "immediate"}}
```

Each leaf resolves **run override → thread override → selected agent revision → deployment default**. Use `config.execution` on the agent, `execution` on thread creation or PATCH, and `options.execution` on a message. Thread PATCH requires `If-Match`: omitted fields stay unchanged, null leaves inherit, and `{"execution":null}` clears the entire override.

A run freezes the effective policy at acceptance, including queued acceptance. Recovery, handoff, resume and automatic continuation keep it. Steers may omit or repeat matching leaves; a change returns `execution_policy_immutable`. Submit with `delivery: next_run` for a different policy. Earlier retained calls stay accessible even if the new run uses inline mode.

Console exposes **Execution policy** in agent configuration and **Session actions** for thread defaults. The new-session **Options** and idle composer's policy controls apply to one run; **Details → Execution policy** shows frozen values. Foreground wait accepts 0–300 seconds and controls observation, not an execution deadline.

### Command environment variables

Use `execution.environment` at the same three layers for non-secret command defaults:

```json
{"execution": {"environment": {"APP_MODE": "staging", "EMPTY": ""}}}
```

Each key resolves **command → run → thread → agent → frozen environment recipe**. Thread PATCH replaces a supplied map; null or `{}` clears that layer. Empty strings set empty values, and removing a key reveals lower defaults rather than unsetting it. Explicit command unsets follow provider support. Use consistent name spelling on Windows.

The run freezes its agent/thread/run overlay. Shell commands, background processes and their ordinary children receive it without changing global sandbox variables. Inline children share it; async children inherit the frozen policy, and retained-snapshot forks keep it. Ordinary forks resolve their selected agent and choices. Keep credentials in the appropriate secret store.

## Retained tool executions

With automatic handoff, a supported call continues after the model receives an `exec-*` receipt. Read that reference's result rather than starting the call again. A retained CodeAct invocation includes its nested calls.

Envd-backed Shell executions survive a worker replacement while the original daemon generation and Envd Session remain available. The replacement worker observes the same process under the original execution ID, including when the start response was lost. It uses the originating run's captured mount and current authorization without starting another command or run. Temporary connection loss leaves recovery pending. A lost daemon generation, expired Envd Session or missing native evidence ends the execution as `lost` with uncertain effects. Other in-process calls, including suspended CodeAct continuations, become `lost` when their worker disappears.

A thread reuses compatible Envd Sessions across runs and human-input pauses. Active work maintains keepalive; final release starts the Session's idle window, one hour by default. Changed bindings or execution policies use separate Sessions. Runs remain on the worker holding the thread's live execution context until its ownership is released or taken over.

Open **Tool executions** from the Console session menu or run details. Use **Inspect** for metadata and **Read result** for a bounded result fragment. API operations are under `/threads/{thread_id}`:

| Endpoint                                      | Action                                                   |
| --------------------------------------------- | -------------------------------------------------------- |
| `GET /tool-executions`                        | List calls, optionally filtered by `run_id` or `active`. |
| `GET /tool-executions/{execution_id}`         | Inspect status.                                          |
| `GET /tool-executions/{execution_id}/result`  | Read with `offset`, `limit` and `version`.               |
| `POST /tool-executions/{execution_id}/cancel` | Request cancellation.                                    |

References belong to one exact thread. Agent result retrieval or first mailbox delivery starts the delivered-result retention window: one hour by default, bounded by 24 hours after completion. Console and HTTP inspection, including result reads, leave delivery and retention unchanged. Repeated reads do not extend retention; expired records return 404.

Cancellation acceptance is separate from completed cleanup. Stopping the originating run requests cancellation of its retained calls and suppresses late-result wakeups. Stopping a later run affects that run's calls, not older calls. Mounts stay in use until retained work and cleanup finish.
