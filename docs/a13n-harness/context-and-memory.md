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
from a13n_harness.providers import ProviderCatalog
from a13n_harness.providers.memory.builtins import MEM0_OSS

catalog = ProviderCatalog((MEM0_OSS,))
definition = catalog["mem0_oss"]
async with definition.open({"base_url": mem0_url}, {"api_key": mem0_api_key}) as backend:
    capability = MemoryCapability(backend=backend, auto_recall=False, toolset=False)
    # Or call backend operations directly with trusted MemorySubject values.
```

Installed extensions contribute `ProviderManifest.memory` alongside Model and Web through the same `a13n_harness.providers.plugins` entry point. A host calls `load_provider_plugins(("acme",))` and explicitly builds a catalog from the selected definitions. Installation does not enable a package. Duplicate types fail; unselected entry points remain unloaded. Hosted applications select an authorized [Memory Provider resource](../a13n-service/memory.md) instead of embedding credentials in an Agent.

The implemented built-ins are `mem0_oss`, `mem0_platform`, and the document-only `filesystem`. OSS uses HTTP directly and needs no Platform SDK. Install `a13n-harness[mem0]` for Platform. Its native SDK loads only when used; local construction defers the SDK's synchronous validation ping. Neither Mem0 backend automatically retries uncertain writes. Each definition declares `supports_records`, `supports_documents`, `supports_revisions`, and `supports_changes`; a definition without record support has no `open_backend`, declares `FilesystemMemoryConfiguration`, and opens only through the Host's own file binding.

The public names are `MemoryCapability`, `MemoryEntry`, and `MemoryScope`, with Capability ID `a13n.memory`. There are no compatibility aliases for the earlier Mem0-only Capability. Backend objects, credentials, and transports are not serialized in `AgentSpec` or `HarnessState`.

### Embedded filesystem documents

`FilesystemMemoryStore` in `a13n_harness.providers.memory.filesystem.store` stores Markdown revisions with validated JSON frontmatter through a borrowed Environment `FileOperator`. It never opens a Worker-local path or executes shell commands. The Host supplies a root-confined, incarnation-pinned file operator, the exact storage identity, trusted subject and principal, and callbacks that check current read/write and source authority. The `filesystem` definition is selectable from the same Memory catalog; its empty credential schema is `forbidden` authentication and does not grant Environment access. Open it with `open_filesystem_store(configuration, binding=FilesystemMemoryBinding(...))`; `definition.open()` rejects this document-only backend.

Service and Console expose this backend through document entries. Embedded Hosts can use the same store directly. A raw filesystem operator alone is not a complete hosted memory binding: the Host also supplies current authority, stable target identity, and conditional publication.

Writes require a Host-supplied `MemoryFileCoordinator`. Its transaction coordinates cooperating writes and erasure across processes, keeps the exact storage binding, settles issued I/O before releasing ownership, and publishes complete bytes conditionally and durably. An in-process lock, a lease without storage fencing, or ordinary `FileOperator.write_text()` alone does not implement that contract. `EnvironmentMemoryFileCoordinator` in `a13n_harness.providers.memory.filesystem.commit` stages one operation and publishes through the optional Environment `commit` capability. POSIX Direct Local and current EIP/envd adapters implement that capability; unsupported adapters fail explicitly. Set the coordinator root to `store.subject_root`. Native OS locking serializes cooperating commits across processes, and all observed content digests are checked before ordered publication. This is not an all-or-nothing multi-file transaction and does not serialize shell or ordinary file edits. Without one, document reads remain available, writes fail before mutation, and model write tools are omitted.

Call `initialize()` only after the Host has explicitly admitted a new corpus. Ordinary reads and reconnection verify its marker and never recreate a missing store. The default configured root is `/memory` in the selected Environment; a configured Environment ID/root must match the supplied binding. Environment adapters remain Host-owned and are not closed by the memory factory.

The current implementation includes:

- `semantic`, `procedural`, and immutable `episodic` documents; legacy `daily`/`long_term` writes are rejected rather than classified implicitly;
- exact-version reads, revision history, and conditional replace/append/edit/patch operations;
- request-key recovery for acknowledged and uncertain publication, including append without duplication;
- digest-bound navigation metadata, lexical substring search including Chinese, AST-based headings, and bounded UTF-8 reads;
- deletion tombstones followed by erasure of revisions, content-bearing changes, and derived metadata;
- one to 1,000 document heads per navigation/search scan, at most 1,000 revisions per document, and an 8 MiB content scan ceiling for lexical search. Exceeding a ceiling reports unavailable retrieval rather than a complete-looking partial result.

Bodies and metadata must fit 256 KiB, reads return at most 32 KiB, and computed diffs have an additional 10,000-line input ceiling. A successful mutation reports committed identity separately from `indexed`; failed derived-cache publication does not undo a committed document. Missing indexes are reconstructed from committed revisions during retrieval. Revisions and commit records are authoritative; caches are not. Out-of-band changes to a revision fail digest verification rather than becoming trusted history.

The Host can list documents and apply bounded organization plans through `a13n_harness.providers.memory.filesystem.organization`. The Host owns durable completion admission, model extraction, current authorization, retry scheduling, and opt-in policy; the embedded store does not start background model calls itself. The complete document-memory specification also describes physical navigation trees, metadata edits, source correction workflows, and management change queries. Those surfaces remain outside this embedded store. `supports_changes` remains false: retaining internal commit diffs alone does not claim the complete public change-query and retention contract.

### Multiple embedded entries

One Capability can combine separately prepared record and document entries:

```python
from a13n_harness.capabilities import MemoryCapability, MemoryEntry

memory = MemoryCapability(
    entries=(
        MemoryEntry(
            name="preferences",
            mode="records",
            description="User preferences and personal facts.",
            capability=MemoryCapability(backend=mem0_backend),
        ),
        MemoryEntry(
            name="project",
            mode="documents",
            description="Project requirements and operating procedures.",
            capability=MemoryCapability(document_store=filesystem_store),
        ),
    )
)
```

The Host opens `mem0_backend` and prepares `filesystem_store` with the bindings described above before constructing the Capability. Entries require distinct names, explicit modes, and nonblank authored purposes; one to sixteen entries are accepted. Multiple entries of the same mode are supported. Tools and permission identities are namespaced independently, such as `preferences_memory_add` and `project_memory_read`. Document references include their entry, such as `memory://project/mdoc_...`; another entry rejects that reference even if it happens to point at the same corpus.

Entries contribute peer guidance and distinct untrusted context blocks under a shared 64 KiB memory budget. Oversized document navigation is explicitly deferred to that entry's index tool. Required document-index failure stops before model work; optional failure leaves an explicit unavailable projection. Native entries retain their bounded once-per-run recall. Host-owned typed record calls select an entry explicitly, for example `await memory.add(ctx, text, entry="preferences", scope=MemoryScope.USER)` on the current run Capability.

Entry names route tools; they do not change storage namespaces. There is no implicit synchronization, dual writing, cross-entry transaction, or fallback. The existing single-backend constructor remains supported for existing embedded Hosts and retained Service integrations.

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

Connector implementations are also reusable through `a13n_harness.providers.connector`. Select an installed `ProviderManifest.connector` definition, call its `open(configuration, credential)`, then discover apps, start setup with a `SetupContext`, inspect the external account, and use its bound tools. The context needs an external user correlation and Connector key, not Service attempt IDs. The [installed provider example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) shows direct typed credentials, account binding and execution. Managed Service use adds IAM, encrypted persistence, durable setup recovery and current dispatch guards around the same implementation.
