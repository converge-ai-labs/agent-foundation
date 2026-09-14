# Context and memory

An agent needs relevant input now and enough state to continue later. Harness keeps those jobs separate: context projections prepare the current model request; compaction changes history; working state holds structured task facts; external memory has its own store.

## Choose the right mechanism

| Need                                    | Mechanism                             | What it does not provide                 |
| --------------------------------------- | ------------------------------------- | ---------------------------------------- |
| Current time, usage, workspace metadata | Runtime context and workspace outline | A scan of all file contents              |
| Current instructions or selected files  | File context                          | Unlimited recursive repository ingestion |
| Long conversations                      | Handoff and compaction                | Recovery of unsaved side effects         |
| Tasks and notes across turns            | Working state in `HarnessState`       | Cross-worker scheduling or locks         |
| Recall across conversations             | Opt-in Mem0 integration               | Automatic durable transcript acceptance  |
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

## Mem0 Long-Term Memory

Memory is opt-in. OSS is the primary backend: open a native transport at Host startup and pass it to `Mem0Capability`. The [Service memory guide](../a13n-service/memory.md) covers hosted authorization and deployment. The OSS endpoint must include the pinned PGVector pagination extension described in the repository's `dev/mem0/README.md`.

```python
from a13n_harness.capabilities import Mem0Capability, Mem0Scope
from a13n_harness.capabilities.mem0_backends import open_mem0_oss

async with open_mem0_oss(base_url=mem0_url, api_key=mem0_api_key) as backend:
    capabilities = (
        Mem0Capability(backend=backend, scope=Mem0Scope.USER, recall_limit=5),
    )
    # Build and execute Agents inside this Host-owned transport lifetime.
```

A fixed scope exposes `memory_search`, `memory_list`, and `memory_add` without an entity or scope argument. The Harness resolves `thread` from the current `thread_id`, `agent` from the `agent_id` identity claim, and `user` from the `user_id` claim. With `scope=None`, one automatic recall searches all available scopes and each memory tool accepts only the `thread`, `agent`, or `user` selector; the model never supplies the underlying ID.

The first eligible input in each logical run performs at most one bounded recall. Recalled records enter only as an untrusted input preamble. They can remain in history as a record of what the model observed, not as restored memory authority. Internal model recovery reuses the same result. `memory_add` stores exactly the supplied bounded text with Mem0 inference disabled; update and delete are not model-visible.

There is no implicit environment configuration or Run-owned client. Hosts can pass trusted `scope_ids` to replace claim-derived values with their own tenant-isolated IDs. OSS performs bounded concurrent per-scope searches and combines the results; a failed scope never yields successful partial recall. Listing uses native cursor pages.

Platform is an independent adapter, not an OSS compatibility mode:

```python
from a13n_harness.capabilities.mem0_backends import open_mem0_platform

async with open_mem0_platform(api_key=platform_api_key) as backend:
    capabilities = (Mem0Capability(backend=backend, scope=Mem0Scope.USER),)
    # Build and execute Agents here.
```

Both context managers own one Host lifetime. The Capability borrows the backend and never closes it. Optional recall failures omit recalled context; required recall fails before model work. An unconfirmed write is not safe to repeat blindly: inspect the memory first. The Harness does not automatically write terminal transcripts to memory because a process-local result does not prove durable checkpoint acceptance. Applications that need extraction should enqueue it only after their own successful durable commit.

## Working State

`WorkingStateCapability` can keep tasks and notes inside its portable Capability namespace:

```python
from a13n_harness.capabilities import WorkingStateCapability

capabilities = (WorkingStateCapability(),)
```

This embedded mode is useful for one process-local or state-resumed Agent. Provider mode replaces task storage with a fresh `TaskStateRunCapability`; the provider remains authoritative, while Harness events report bounded committed deltas.

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
