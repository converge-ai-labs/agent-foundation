# Context and memory

An agent needs relevant input now and enough state to continue later. Harness keeps those jobs separate: context projections prepare the current model request; compaction changes history; working state holds structured task facts; external memory has its own store.

## Choose the right mechanism

| Need                                    | Mechanism                             | What it does not provide                 |
| --------------------------------------- | ------------------------------------- | ---------------------------------------- |
| Current time, usage, workspace metadata | Runtime context and workspace outline | A scan of all file contents              |
| Current instructions or selected files  | File context                          | Unlimited recursive repository ingestion |
| Long conversations                      | Handoff and compaction                | Recovery of unsaved side effects         |
| Tasks and notes across turns            | Working state in `HarnessState`       | Cross-worker scheduling or locks         |
| Recall across conversations             | Opt-in Memory Capability              | Automatic durable transcript acceptance  |
| Smaller requests after idle time        | Cold-start filter                     | Provider cache-lifetime detection        |

A model context budget does not enable tools by itself. Select the corresponding Capability, then configure its thresholds. For continuation serialization and human decisions, use [State and Resume](state-and-resume.md).

## Context Composition

Harness context features use one model-context coordinator, so each owner contributes a bounded block without directly rewriting another owner's messages.

A practical general-purpose context composition is:

```python
from a13n_harness.capabilities import (
    FileContextCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
)

capabilities = (
    RuntimeContextCapability(),
    WorkspaceOutlineCapability(),
    FileContextCapability(),
)
```

- Runtime context is refreshed for each request and can expose only explicitly selected metadata keys.
- Workspace outline reads metadata, not file content, and appears only on input requests.
- File context loads selected files once for the logical run and fences the Environment route used to load them.

All three have explicit byte, item, depth, or line bounds. Configure them to match the Environment and target model rather than treating their defaults as universal.

For context lifecycle features, callers supply Harness-managed policy through the `AgentSpec.model_characteristics` construction key. An explicit Harness context window is projected onto the effective native `ModelProfile`. `HandoffCapability()` uses it to resolve its 65% reminder during build. An otherwise unconfigured `CompactionCapability()` resolves its 90% threshold at each request, preferring native `RunContext` context-window and usage values before falling back to Harness characteristics and captured provider usage. Explicit token settings override these values, and the Capabilities remain opt-in.

## Long-Term Memory

Memory is opt-in. `MemoryCapability` works with any implementation of the typed `MemoryBackend` contract. Mem0 OSS and Platform are built-in adapters. For OSS, open a native transport in your Host and pass it to the Capability. The [Service memory guide](../a13n-service/memory.md) covers hosted authorization and deployment. The OSS adapter calls public native endpoints on an existing deployment; no source patch or special server image is required. Its list operation is bounded, not paginated.

```python
from a13n_harness.capabilities import MemoryCapability, MemoryScope
from a13n_harness.providers.memory.mem0_oss import open_mem0_oss

async with open_mem0_oss(base_url=mem0_url, api_key=mem0_api_key) as backend:
    capabilities = (
        MemoryCapability(backend=backend, scope=MemoryScope.USER, recall_limit=5),
    )
    # Build and execute Agents inside this Host-owned transport lifetime.
```

A fixed scope exposes `memory_search`, `memory_list`, and `memory_add` without an entity or scope argument. The Harness resolves `thread` from the current `thread_id`, `agent` from the `agent_id` identity claim, and `user` from the `user_id` claim. With `scope=None`, one automatic recall searches all available scopes and each memory tool accepts only the `thread`, `agent`, or `user` selector; the model never supplies the underlying ID.

The first eligible input in each logical run performs at most one bounded recall. Recalled records enter only as an untrusted input preamble. They can remain in history as a record of what the model observed, not as restored memory authority. Internal model recovery reuses the same result. `memory_add` stores exactly the supplied bounded text with Mem0 inference disabled; update and delete are not model-visible.

There is no implicit environment configuration or Run-owned client. Hosts can pass trusted `scope_ids` to replace claim-derived values with their own tenant-isolated IDs. OSS performs bounded concurrent per-scope searches and combines the results; a failed scope never yields successful partial recall. OSS listing uses native `GET /memories` with a result limit and no cursor; Platform supports native cursor pages. Neither tool results nor a short OSS list prove that the full memory collection was loaded.

Platform is an independent adapter, not an OSS compatibility mode:

```python
from a13n_harness.providers.memory.mem0_platform import open_mem0_platform

async with open_mem0_platform(api_key=platform_api_key) as backend:
    capabilities = (MemoryCapability(backend=backend, scope=MemoryScope.USER),)
    # Build and execute Agents here.
```

Both context managers own one Host lifetime. The Capability borrows the backend and never closes it. Optional recall failures omit recalled context; required recall fails before model work. An unconfirmed write is not safe to repeat blindly: inspect the memory first. The Harness does not automatically write terminal transcripts to memory because a process-local result does not prove durable checkpoint acceptance. Applications that need extraction should enqueue it only after their own successful durable commit.

### Custom memory behavior

Set `auto_recall=False` and `toolset=False` when your own Capability owns recall or tools. These flags do not remove the selected backend. Existing Harness plugins contribute custom Capabilities through `get_capabilities()`; there is no separate memory behavior registry and no second public memory attachment.

Use the finalized Capability in the current native `RunContext`, not an original construction object or private context cache:

```python
from a13n_harness import AgentContext
from a13n_harness.capabilities import MemoryCapability, MemoryScope
from a13n_harness.providers.memory.contracts import MemoryRecord
from pydantic_ai import RunContext


async def remember_user_preference(
    ctx: RunContext[AgentContext], text: str
) -> MemoryRecord:
    memory = ctx.capabilities.get(MemoryCapability.id)
    if not isinstance(memory, MemoryCapability):
        raise ValueError("This Agent has no selected memory backend")
    return await memory.add(ctx, text, scope=MemoryScope.USER)
```

The same API exposes typed `search`, `list`, `get`, `update`, and `delete` operations. A fixed-scope Capability does not require a scope argument; otherwise each call selects one available scope. Records expose `id`, `text`, `subjects`, and optional `score`. List returns a `MemoryPage`: `pagination=None` means bounded results of unknown completeness; `pagination.next_cursor=None` means the last native page. OSS lists up to 1000 records, and Platform caps each native page at 200. Search is independent of the listed subset.

Custom post-commit jobs and management services can call an authorized `MemoryBackend` directly; they do not need to create a Capability or run an Agent. Text is nonblank, at most 8000 characters, and preserved verbatim. Writes verify readback, and `MemoryWriteUnconfirmed` requires inspection before repetition. A delete does not remove already observed text from context or traces.

### Provider definitions

Reusable storage definitions live under `a13n_harness.providers.memory`. Each `MemoryProviderDefinition` supplies typed configuration and credential models, the shared declarative authentication and help metadata, and an asynchronous `open_backend` callback. The callback owns its backend lifetime. Metadata loading opens no backend and performs no vendor I/O.

```python
from a13n_harness.providers.memory import MemoryProviderCatalog
from a13n_harness.providers.memory.builtins import MEM0_OSS

catalog = MemoryProviderCatalog((MEM0_OSS,))
definition = catalog["mem0_oss"]
async with definition.open({"base_url": mem0_url}, {"api_key": mem0_api_key}) as backend:
    capability = MemoryCapability(backend=backend, auto_recall=False, toolset=False)
    # Or call backend operations directly with trusted MemorySubject values.
```

Installed extensions contribute `ProviderManifest.memory` alongside Model and Web through the same `a13n_harness.providers.plugins` entry point. A host calls `load_provider_plugins(("acme",))` and explicitly builds a catalog from the selected definitions. Installation does not enable a package. Duplicate types fail; unselected entry points remain unloaded. Hosted applications select an authorized [Memory Provider resource](../a13n-service/memory.md) instead of embedding credentials in an Agent.

The implemented built-ins are `mem0_oss` and `mem0_platform`. OSS uses HTTP directly and needs no Platform SDK. Install `a13n-harness[mem0]` for Platform. Its native SDK loads only when used; local construction defers the SDK's synchronous validation ping. Neither backend automatically retries uncertain writes. The document-store abstraction and document behavior remain separate from Provider construction; no filesystem Provider is currently registered.

The public names are `MemoryCapability` and `MemoryScope`, with Capability ID `a13n.memory`. There are no compatibility aliases for the earlier Mem0-only Capability. Backend objects, credentials, and transports are not serialized in `AgentSpec` or `HarnessState`.

## Working State

`WorkingStateCapability` can keep tasks and notes inside its portable Capability namespace:

```python
from a13n_harness.capabilities import WorkingStateCapability

capabilities = (WorkingStateCapability(),)
```

This embedded mode is useful for one process-local or state-resumed Agent. Provider mode replaces task storage with a fresh `TaskStateBinding` in `RunBindings.task_state`; the provider remains authoritative, while Harness events report bounded committed deltas.

The Notes tools have explicit mutation semantics:

- `note_write(key, value)` creates or updates a note and reports `created` or `updated`;
- `note_delete(key)` is idempotent and reports `deleted` or `already_absent`;
- `note_get(key=None)` reads one complete value or lists sorted keys with a count.

Notes are projected only on user-input boundaries; active Tasks are projected on both user-input and tool-result boundaries. Their bounded request epilogues put Notes first and Tasks last. Existing historical projections remain unchanged, so tool-result rounds do not refresh old Notes. Complete note values appear as `<note>` entries when they fit. A `<note-ref>` means the value is available through `note_get`, while `<notes-omitted>` reports entries outside the projection. Note values are never partially truncated, and empty Notes produce no Notes block. `WorkingStateConfiguration` defaults to at most 256 projected notes and 128 projected tasks within a shared 64 KiB context budget.

Notes preserve structured session facts, Tasks preserve execution state, and `summarize` preserves narrative continuity and the next step. Before a handoff, reconcile stale notes and task statuses; do not copy every note or task into the summary. Automatic compaction likewise replaces history only, after which current Notes and Tasks are projected again. Its nested summary request sees the full unchanged history, including prior overlays and thinking, with no trimming or suffix-selection mode. It preserves `tool_choice` and tells the model not to call any tools. Both compaction and `summarize` replay the current logical run's initial input and delivered user steering in order, preserving multimodal content; pending steering and internal notices are not replayed.

Working state is not a distributed workflow engine. Cross-worker ownership, durable leases, schedules, and delivery belong to the Host or task provider.

## Filters

`MessageIntegrityFilterCapability` is mandatory and builder-owned. `ContentFilterCapability` is optional. Cold-start filtering is enabled by default through `AgentSpec.cold_start_filter`, with a one-hour idle interval:

```python
from a13n_harness import AgentSpec
from a13n_harness.filters import (
    ColdStartFilterConfiguration,
    ContentFilterCapability,
    ContentFilterConfiguration,
)

capabilities = (
    ContentFilterCapability(
        ContentFilterConfiguration(
            accepted_media=frozenset({"image", "document"}),
            max_media_items=16,
        )
    ),
)
spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
without_cold_compression = spec.with_updates(cold_start_filter=None)
```

Use content filtering only for provider/model multimodal compatibility. Cold-start filtering shortens old, already-consumed tool-result strings after the configured interval since the latest model response. It leaves user input, thinking, native media, and pending tool results unchanged. One hour is an intentional retention policy, not a promise about a provider's cache expiry. An explicitly composed `ColdStartFilterCapability` keeps its own policy and suppresses the automatic instance. Neither filter is transport retry, semantic recovery, or long-term memory.

## Handoff versus automatic compaction

`HandoffCapability` exposes an explicit `summarize` tool; `CompactionCapability` reacts to request context usage. Both are optional. Configure their usual thresholds through [model characteristics](models.md#model-characteristics), or supply explicit token thresholds when the Agent needs a fixed policy.

A summary is narrative continuation, not a substitute for task/note state. Neither summarization nor compaction commits application storage. Persist the resulting safe `HarnessState` only under your Host's acceptance policy.

Provider context managers own cleanup for the resources they acquire. Protect asynchronous client teardown with a bounded cancellation shield inside the provider, as shown in the installed Acme example. The definition and Service compose provider contexts normally so TaskGroups retain their scope nesting; they do not impose a universal exit timeout. Borrowed clients remain owned by their caller.
