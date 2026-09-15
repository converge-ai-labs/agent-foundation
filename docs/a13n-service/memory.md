# Long-term memory

Service provides tenant-scoped memory through reusable **Memory Providers**, without storing memory content in its database. **Mem0 OSS is the primary built-in backend**; Mem0 Platform uses an independent native SDK adapter. Each Agent revision explicitly selects a Provider. Credentials are encrypted, write-only Provider fields, never Agent JSON, model prompts, or Run state.

Memory is optional: starting Service does not require a Mem0 server, credentials, or an Agent memory selection. Local `make dev` and `make setup` leave it disabled unless you manually enable `dev/mem0/local.toml`; `MEM0_CONFIG` selects an alternative local configuration. Native integration tests also require separate explicit opt-in. See the [local OSS walkthrough](https://github.com/converge-ai-labs/agent-foundation/tree/main/dev/mem0) for startup and configuration.

## Create a Memory Provider

For OSS, connect an existing native Mem0 server providing `GET /memories` with `top_k`, `POST /search`, and memory CRUD. No upstream patch, custom route, replacement image, or database access is required. The server separately owns its embedding/LLM configuration and storage. Service does not proxy model configuration or migrate embeddings.

1. Read available definitions from `GET /api/v1/memory-provider-types`. Built-ins are `a13n.mem0-oss` and `a13n.mem0-platform`; installed external packages appear only after deployment selection.
2. Create a Workspace Provider with `POST /api/v1/workspaces/{workspace}/memory-providers`:

```json
{
  "type": "a13n.mem0-oss",
  "name": "Team memory",
  "configuration": {"base_url": "http://mem0:8000"},
  "credential": {"api_key": "<server API key>"},
  "enabled": true
}
```

Supply the real credential through your authenticated management client; do not commit it. The response includes a stable `memprov_*` ID, configuration, and `credential_configured`, but never the credential. To share one Provider across Workspaces, create it through the corresponding Organization collection. Records remain Workspace-isolated.

For Platform, use `type: "a13n.mem0-platform"`, `configuration: {}`, and a Platform API key. An optional `base_url` overrides its native API endpoint. There is no automatic backend fallback.

Process configuration controls only the operation deadline:

```toml
[memory]
timeout_seconds = 30
```

Control and Worker use the same pinned Provider packages and `provider_plugins.enabled` selection. They acquire resource credentials at dispatch rather than keeping a deployment-wide memory client. See [configuration reference](configuration-reference.md#memory) for the timeout and [deployment extensions](configuration.md) for package selection.

## Enable an Agent

Include this field in the Agent revision's `config`:

```json
{
  "memory": {
    "provider_id": "memprov_0123456789abcdef0123",
    "scope": "thread",
    "auto_recall": true,
    "toolset": true,
    "recall_limit": 5,
    "recall_timeout": 2,
    "recall_required": false
  }
}
```

Replace `provider_id` with the ID returned when you created the Provider. Creating a Provider alone enables no Agent memory. Omission or null disables memory. A Run override can replace the whole selection or disable it with null. Child Agents retain their own selection, rather than implicitly inheriting the parent's memory configuration.

- `thread` persists across Runs in the same Service Thread.
- `agent` shares memory across that Agent's Revisions, not across unrelated Agents.
- `user` belongs to the authenticated human User within the current Workspace; service accounts cannot use it.
- Null `scope` recalls the union of available scopes and lets tools select a scope kind. The model never supplies the underlying ID.

The first eligible input performs one bounded recall. Optional failure omits recalled context; `recall_required=true` fails before model work. Recalled text is untrusted context. `memory_search`, `memory_list`, and `memory_add` are the only memory tools. Add stores explicit nonblank text verbatim with inference disabled. There is no automatic transcript extraction.

## Manage records

The Native API uses `/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories` with `scope` in the query. For `agent` or `thread`, also provide `subject_id` using the immutable Service ID. For `user`, omit `subject_id`: it always comes from the authenticated User.

| Operation | Request                                                  |
| --------- | -------------------------------------------------------- |
| Create    | POST collection with `{ "text": "A fact to remember" }`  |
| List      | GET collection with optional `limit` and `cursor`        |
| Search    | POST `/search` with `{ "query": "fact", "limit": 20 }`   |
| Read      | GET `/{memory_id}`                                       |
| Update    | PUT `/{memory_id}` with `{ "text": "Replacement text" }` |
| Delete    | DELETE `/{memory_id}`                                    |

All operations require current authorization. Viewer can read; Runner can write. Direct Agent grants cover that Agent and its current Threads, not the User's Workspace-wide memory. Provider IDs alone grant no access. Records expose only `id`, `memory`, and nullable `score`.

Lists load at most `limit` records (1–1,000; default 1,000). The response contains `items` and `pagination`:

- **OSS:** `pagination` is null. Native `GET /memories?top_k=1000` loads a bounded subset, not the first page of an available traversal. A cursor is rejected. Neither a short nor an empty result proves that no other records exist, especially with native expiry filtering.
- **Platform:** `pagination` is an object containing `next_cursor`. Requests respect the native 200-record page ceiling even when a larger limit is requested. Pass the cursor with the same Provider, scope, and limit; null `next_cursor` within the object marks the final native page.
- **Search:** `pagination` is null on both backends. It retrieves relevant stored memories independently of the management list and is not a full export API.

A frontend can display 20-record local pages from the OSS response, matching the upstream Dashboard approach. These pages only slice the already-loaded data; label counts as "loaded memories", never a complete total. Display a hint such as:

> Showing up to 1,000 loaded memories. More may exist; search for relevant memories.

Use the requested limit instead of 1,000 when the caller selects a smaller bound. This is a data-completeness hint, not a backend readiness or development-status message. The bound does not limit how many memories the provider stores or searches. This backend integration does not add frontend implementation.

Create and update verify exact text by reading it back. Delete verifies absence. A timeout or failed verification can leave a committed change: `memory_write_unconfirmed` means inspect the record before repeating, not retry automatically. No idempotency key or background reconciliation is provided for memory writes.

Deleting memory does not erase text already observed in Thread history, exported context, or traces. Those surfaces have separate retention policies.

## Rotate credentials and change providers

Read a Provider to obtain its `ETag`, then PATCH its owning collection's item route with `If-Match`. You may change `name`, `enabled`, or `credential`. Configuration and type are immutable: a different storage target requires a new Provider. A stale ETag is rejected rather than overwriting another change. `/references` lists visible Agent Revision references, including non-current revisions.

Key rotation takes effect on the next dispatch, including an already accepted Run. Disabling a Provider prevents subsequent operations but does not delete remote records. Two resources pointing at the same endpoint still have different namespaces. To share memory intentionally, select the same resource and the same authorized Workspace/subject.

A Run retains the root and child Provider IDs accepted with its Agent graph. Editing an Agent later does not redirect an existing Run. The content API always names the Provider explicitly, so previously selected resources remain addressable after changing an Agent selection. Provider read/manage permissions govern discovery and configuration; memory read/write permissions independently govern record access.

This is a pre-public breaking replacement of process-wide memory configuration. Remove `memory.provider`, `memory.base_url`, and `memory.api_key` from Service configuration and stop supplying their former environment variables. Create a Provider resource and update each opted-in Agent to select its ID. The new namespace includes the Provider resource ID; old process-scoped namespaces are not automatically migrated or treated as a fallback. If preserving old memory is required, arrange explicit data migration with the storage operator before switching. A bounded OSS list is not a complete migration/export source.

## Implement an external backend

Use the Harness `MemoryBackendPlugin` contract for configuration, credentials, and an async backend lifetime. The backend implements typed search, list, add, get, update, and delete, including subject checks and write confirmation. `MemoryBackendPlugin` is the neutral extension point, not a Mem0 API replica. The concrete `Mem0OSSBackendPlugin` and `Mem0PlatformBackendPlugin` factories are built-in adapters; vendor names belong to those implementations, not to the public Memory feature. Register the same plugin object with the Service Provider package's `registry.memory.register(...)`; no second Service factory is needed. Select that package through `provider_plugins.enabled` on each admitting/executing role. Registration and schema validation perform no I/O.

See [Harness long-term memory](../a13n-harness/context-and-memory.md#long-term-memory) for the shared contracts and embedded usage. The [Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) demonstrates the installed package entry point. Harness behavior plugins remain separate and Worker-owned; registering a Memory backend does not authorize arbitrary Agent behavior or contribute Service routes or database tables.
