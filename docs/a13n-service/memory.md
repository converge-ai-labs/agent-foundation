# Long-term memory

Service provides tenant-scoped Mem0 memory without a second copy in its database. **OSS is the primary backend**; Platform uses an independent native SDK adapter. Memory is opt-in for each Agent revision. Backend credentials belong to process configuration, not Agent JSON or model prompts.

## Configure the backend

For OSS, deploy the pinned native image and PGVector pagination extension from `dev/mem0/` in the repository. Configure the same endpoint and credentials on Control and Worker:

```toml
[memory]
provider = "oss"
base_url = "http://mem0:8000"
timeout_seconds = 30
```

Supply `A13N_SERVICE_MEMORY_API_KEY` through deployment secret injection. Do not commit keys. The native server separately owns its embedding/LLM configuration and storage. Service does not proxy model configuration or migrate embeddings when you change backends.

For Platform, select `provider = "platform"`, supply its API key through the same secret setting, and omit `base_url` to use the SDK default. There is no automatic fallback between providers. `provider = "none"` disables the deployment backend. See [configuration reference](configuration-reference.md#memory) for all settings.

## Enable an Agent

Include this field in the Agent revision's `config`:

```json
{
  "memory": {
    "scope": "thread",
    "auto_recall": true,
    "toolset": true,
    "recall_limit": 5,
    "recall_timeout": 2,
    "recall_required": false
  }
}
```

Omission or null disables memory. A Run override can replace the whole selection or disable it with null. Child Agents retain their own selection, rather than implicitly inheriting the parent's memory configuration.

- `thread` persists across Runs in the same Service Thread.
- `agent` shares memory across that Agent's Revisions, not across unrelated Agents.
- `user` belongs to the authenticated human User within the current Workspace; service accounts cannot use it.
- Null `scope` recalls the union of available scopes and lets tools select a scope kind. The model never supplies the underlying ID.

The first eligible input performs one bounded recall. Optional failure omits recalled context; `recall_required=true` fails before model work. Recalled text is untrusted context. `memory_search`, `memory_list`, and `memory_add` are the only memory tools. Add stores explicit nonblank text verbatim with inference disabled. There is no automatic transcript extraction.

## Manage records

The Native API uses `/api/v1/workspaces/{workspace}/memories` with `scope` in the query. For `agent` or `thread`, also provide `subject_id` using the immutable Service ID. For `user`, omit `subject_id`: it always comes from the authenticated User.

| Operation | Request                                                  |
| --------- | -------------------------------------------------------- |
| Create    | POST collection with `{ "text": "A fact to remember" }`  |
| List      | GET collection with optional `limit` and `cursor`        |
| Search    | POST `/search` with `{ "query": "fact", "limit": 20 }`   |
| Read      | GET `/{memory_id}`                                       |
| Update    | PUT `/{memory_id}` with `{ "text": "Replacement text" }` |
| Delete    | DELETE `/{memory_id}`                                    |

All operations require current authorization. Viewer can read; Runner can write. Direct Agent grants cover that Agent and its current Threads, not the User's Workspace-wide memory. Provider IDs alone grant no access. Records expose only `id`, `memory`, and nullable `score`.

Lists return `items` and `next_cursor`. Pass the cursor with the same scope and page size; stop only at null, not at an empty page. OSS pages use native UUID keyset order without a fixed total limit. Expired rows can produce empty pages with continuation. Concurrent inserts before the cursor are visible on a new traversal, not a guaranteed snapshot.

Create and update verify exact text by reading it back. Delete verifies absence. A timeout or failed verification can leave a committed change: `memory_write_unconfirmed` means inspect the record before repeating, not retry automatically. No idempotency key or background reconciliation is provided for memory writes.

Deleting memory does not erase text already observed in Thread history, exported context, or traces. Those surfaces have separate retention policies.
