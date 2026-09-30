---
title: Context and working state
description: Separate what the model sees now from history compaction and structured working state.
---

An agent needs relevant input now and enough state to continue later. Harness keeps those jobs separate: context projections prepare the current model request; compaction changes history; working state holds structured task facts.

## Choose the right mechanism

| Need                                    | Mechanism                             | What it does not provide                 |
| --------------------------------------- | ------------------------------------- | ---------------------------------------- |
| Current time, usage, workspace metadata | Runtime context and workspace outline | A scan of all file contents              |
| Current instructions or selected files  | File context                          | Unlimited recursive repository ingestion |
| Long conversations                      | Handoff and compaction                | Recovery of unsaved side effects         |
| Tasks and notes across turns            | Working state in `HarnessState`       | Cross-worker scheduling or locks         |
| Durable files shared by conversations   | [File memory](memory.md)              | Semantic search or silent merging        |
| Facts recalled by similarity            | [Record memory](memory.md)            | Record versions or conflict detection    |
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

Use content filtering only for provider/model multimodal compatibility. Cold-start filtering shortens old, already-consumed tool-result strings after the configured interval since the latest model response. It leaves user input, thinking, native media, and pending tool results unchanged. One hour is an intentional retention policy, not a promise about a provider's cache expiry. An explicitly composed `ColdStartFilterCapability` keeps its own policy and suppresses the automatic instance. Neither filter is transport retry or semantic recovery.

Image preparation is also enabled by default. Before each model request, `ImageFilterCapability` splits tall static images into full-width segments (4096 pixels high with 50 pixels of overlap), compresses individual images or segments to 5 MiB of base64-encoded bytes and an 8000-pixel maximum axis, and keeps the newest 20 images. Corrupted or unpreparable images and older excess images become explanatory text in that request only. Saved history and original files retain their pixels and metadata.

```python
from a13n_harness.filters import ImageFilterConfiguration

spec = AgentSpec(image_filter=ImageFilterConfiguration(max_images=10, split_large_images=False))
without_image_preparation = spec.with_updates(image_filter=None)
```

Set `max_image_bytes=0` or `max_image_dimension=0` to disable that compression limit independently, and `support_gif=False` to remove binary GIF input. Animated images are not split or flattened into JPEG. The policy covers native user sequences and ordinary tool-return scalar or top-level list images; nested tool JSON is not reinterpreted as an image input. Image URLs count toward the quota but are not fetched or transformed. The policy does not impose a total request-byte budget or guarantee acceptance by every gateway. `AgentMediaUnderstandingProvider` selects its image target's policy independently through its own `image_filter` argument; it never inherits the parent's limits.

## Handoff versus automatic compaction

Both handoff and compaction ask the Agent to carry forward the `SKILL.md` paths, brief roles, and immediately needed supporting file paths for skills actually used and still relevant. On resume, the Agent is reminded to reread those instructions before dependent work unless the full content is already available. This is guidance, not an automatic reload or tool gate; merely inspected skills are not promoted to active workflows, and complete reads still in context can be reused.

`HandoffCapability` exposes an explicit `summarize` tool; `CompactionCapability` reacts to request context usage. Both are optional. Configure their usual thresholds through [model characteristics](models.md#model-characteristics), or supply explicit token thresholds when the Agent needs a fixed policy.

A summary is narrative continuation, not a substitute for task/note state. Neither summarization nor compaction commits application storage. Persist the resulting safe `HarnessState` only under your Host's acceptance policy.
