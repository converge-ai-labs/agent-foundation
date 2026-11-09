---
title: Memory
description: Save knowledge, reuse it in another conversation, and inspect what agents changed.
---

Use memory for preferences, decisions and reference facts that should outlive a conversation. Start with a Memory Space for shared knowledge and session notes. Choose a standalone memory when your application needs to attach a particular store directly to a thread.

| Need                                                 | Choose                                                              |
| ---------------------------------------------------- | ------------------------------------------------------------------- |
| Shared project knowledge with separate session notes | [Memory Space](#memory-spaces)                                      |
| A versioned file tree mounted on selected threads    | [Standalone file memory](standalone-memory.md#create-a-file-memory) |
| Short facts retrieved by meaning from mem0           | [Record memory](standalone-memory.md#record-memories)               |

Memories belong to a workspace and need no compute environment. The examples use `A13N_URL` for the Service base URL and `A13N_API_KEY` for a workspace-scoped key. API paths below are under `/api/v1`.

## Memory Spaces

A **Memory Space** keeps shared knowledge and session notes in one versioned file store. Try a complete save-and-reuse workflow before configuring automatic organization:

1. Open **Memories → Spaces** and create a Space named `Product knowledge`.
2. In an agent's **Memory** section, enable participation. Select that Space with the mount name `product` and write access. Keep the agent's memory tools enabled.
3. Start a new session with the agent. Ask: “Save our documentation language preference, English, in `global/preferences.md` in the `product` memory.”
4. Open the Space's **Files** and check the saved content. Use **File history** to inspect the author and revision.
5. Start another session with the same Space selected. Ask the agent to read `global/preferences.md` and report the saved preference.

Ordinary runs can access `global/` and their own `sessions/{session_id}/` folder. Put shared knowledge in `global/`; put conversation-specific notes in the session folder. Context includes `global/MEMORY.md` by default and the session's `MEMORY.md` when present. Workspace permissions govern Console and API management of all Space files.

Choose **General** for reusable topic knowledge or **General with daily records** to also retain important events by date. Add business priorities in **Custom guide**. **Advanced settings** contains the time zone and editable **System guide**. Both ordinary agents and the organizer use these rules. Changes apply to new Runs and do not reorganize existing files automatically. Space creation and updates accept the same `guidance` object as [standalone file memories](standalone-memory.md#create-a-file-memory).

### Select Spaces

Create a Space with `POST /memory-spaces` (`write`):

```json
{"name": "Product knowledge"}
```

Use the returned `id` in the agent's `config.memory`, or in `POST /sessions` or `PATCH /sessions/{session_id}`:

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "product", "space_id": "mspace_...", "access": "write"}]
  }
}
```

Session selection needs `run`; PATCH also needs the session's `If-Match`. Each name and Space ID can appear once. Omitted fields inherit agent defaults at the first accepted business run. An explicit `spaces` list replaces the selection; `[]` supplies no Space context or tools. Later agent changes and branches preserve the session's settled choice. Use the session header's **Memory** control to change future runs.

Turning participation off preserves the selection. Disabling a Space preserves its files, history and bindings while excluding it from new runs. Accepted runs keep their selected mounts. Space mounts and standalone thread mounts share the mount limit and must have distinct names.

### Organize saved knowledge

Once the Space contains useful files, configure an organizer model in **Configuration → Organization** and choose **Organize now**. The organizer reads shared knowledge and all session folders, then maintains `global/MEMORY.md`. Review the resulting files and revisions in the **Organization** tab. Each round uses a fresh thread in a dedicated session, only that Space's memory tools, and a 200-model-request ceiling.

Enable **Automatic organization** to process later saved changes. Choose **Any saved change** or **After a number of changes**. For example, this creation or update fragment checks once a minute, requires ten changes and waits fifteen minutes between rounds:

```json
{
  "organization": {
    "enabled": true,
    "model": "your-model-key",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {"type": "change_count", "count": 10}
  }
}
```

Count mode includes saved file changes from agents, Console and API; a rename counts once. Unchanged saves, rejected writes, history cleanup and organizer edits do not count. An admitted round consumes the changes present at its start, including when it fails or is cancelled. Changes saved during the round remain for later. Counts survive restarts and history cleanup; existing stores begin counting new changes when the capability is installed.

**Check interval** defaults to 10 seconds (range 10–86400); **Cooldown** defaults to 300 seconds (range 0–604800). Automatic rounds require both their trigger and cooldown, evaluated on a shared 10-second service sweep. These are minimum delays. Recorded deadlines retain their timing; policy edits affect later checks and rounds. **Organize now** bypasses the interval, threshold and cooldown.

The **Organization** tab shows status, costs, traces and revisions separately from business conversations. Failed or cancelled rounds keep saved edits. A history-gap status means retained revisions cannot establish progress; use **Organize now** to request another round.

For API clients, `GET /memory-spaces/{id}/organization` reads status; `POST /memory-spaces/{id}/organizations` requests a round; `GET` on that path lists rounds. `/memory-spaces/{id}/sessions` lists associated business sessions. Lists use `limit` and `cursor`. File and revision APIs use the Space's `memory_id` under `/memories/{memory_id}`. Manage the backing store through the Space; it stays out of standalone memory lists and cannot be mounted directly.

### Automatically extract conversation knowledge

Automatic policies can include task `instructions` and a `history` selection. Choose conversation Session IDs and an explicit starting seal time (`after`); optional `before` closes the window and `max_runs` limits each batch (default 100, maximum 1000). For example, add this to a Space's `organization` policy:

```json
{
  "instructions": "Maintain product decisions and current handoff notes.",
  "history": {
    "session_ids": ["sess_..."],
    "after": "2026-11-01T00:00:00Z",
    "max_runs": 50
  }
}
```

Unprocessed sealed runs trigger organization even when no files changed or the memory is empty. Completed rounds consume their selected runs; failed and cancelled rounds leave them eligible after cooldown. Each batch keeps its exact source identities. File change counts and history progress are independent, so revision cleanup and concurrent file edits do not replay successfully processed conversations. Changing the selection preserves completed history progress. One-off organization tasks do not advance automatic progress.

Standalone file memories use the same policy without requiring a Space. Read `GET /memories/{id}/organization`, then replace the policy with `PUT` and the returned `If-Match` ETag. The response includes policy configuration, status, `processed_history_runs` and configuration version; status changes leave the policy ETag unchanged. New standalone policies are disabled. Managed backing memories use their Space policy instead. The service rechecks the configuring actor's current permissions and configured resources before admitting every round.

### Run a custom organization task

Use `POST /memory-organizations` to organize a standalone file memory or an enabled Space with your own model and instructions. This needs workspace `read`, `write` and `run` permissions. It works without an automatic policy, including on an empty store:

```json
{
  "target": {"kind": "memory", "id": "mem_..."},
  "model": "your-model-key",
  "instructions": "Consolidate supported product decisions and preserve unresolved disagreements."
}
```

For a Space, use `{"kind": "space", "id": "mspace_..."}`. The managed backing memory cannot be selected directly. The response identifies the organization request and its ordinary run. Read it with `GET /memory-organizations/{id}`; use the run API for status, cancellation, usage and traces. Each submission accepts a distinct task, including a retry after an uncertain response; separate tasks may run concurrently against the same target with ordinary file conflict checks. Each keeps its own instructions and caller authority. Failed or cancelled runs retain committed edits.

The `guidance.modules` API field combines `topics`, `journal` and `session_notes` independently. For example, `{"modules": ["topics", "session_notes"], "timezone": "UTC"}` keeps reusable knowledge and current handoff state. Omit `modules` to use the selected preset; `[]` keeps only common maintenance rules. A system override replaces the complete composed guide. Read the available module text with `GET /memories/guidance-presets`.

### Extract knowledge from selected conversations

Add `history` to a custom organization request to extract knowledge from one or several conversation Sessions, including into an empty memory:

```json
{
  "target": {"kind": "memory", "id": "mem_..."},
  "model": "your-model-key",
  "instructions": "Extract supported product decisions, cite source Runs and retain conflicting statements.",
  "history": {
    "session_ids": ["ses_first", "ses_second"],
    "after": "2026-01-01T00:00:00Z",
    "before": "2026-02-01T00:00:00Z",
    "max_runs": 100
  }
}
```

Select 1–100 unique conversation Sessions in the same workspace. The optional time bounds apply inclusively to Run seal time, not message time. At acceptance, the Service freezes the matching sealed Runs and returns their Session, Thread and Run provenance in `sources`. Later conversation activity does not change that selection. `max_runs` defaults to 100 and supports up to 1000; an overfull selection is refused, so narrow the window or split the Sessions. An empty selection supplies no historical facts.

The organizer has bounded source discovery and content reading/search tools limited to that selection. It treats transcripts as evidence, preserves corrections and disagreements, and distinguishes user statements from agent claims. Omitting `history` organizes saved files only. Upgrading does not scan old conversations or spend model tokens automatically.

### Review a candidate before publishing

Set `"output": "candidate"` on a custom organization request to keep live files unchanged during generation. The response's `candidate_memory_id` names an independent file memory copied from the target. Inspect or edit its files through ordinary memory APIs. This also works with selected conversation history.

1. Wait for the ordinary run to complete and seal. Read `GET /memory-organizations/{id}/candidate` for status, changed paths, `candidate_seq` and `target_seq`.
2. Inspect `GET /memory-organizations/{id}/candidate/diff?path=...`. It compares the admission baseline with the candidate. A `truncated` preview requires opening the full files. Inspect the current target separately when it has changed.
3. Publish with `POST /memory-organizations/{id}/candidate/apply`, using `{"mode": "checked", "candidate_seq": 7, "target_seq": 4}` with the actual reviewed sequences. The complete tree publishes atomically. Unchanged target files retain their IDs and versions.

`review_changed` means the candidate or target changed after review. Reload both before deciding. `baseline_changed` means the target changed since generation began. Generate a fresh candidate, or explicitly choose `replace_all` after reviewing the current target. `replace_all` overwrites the complete target tree and deletes target-only files; it does not merge concurrent edits. The same exact apply request can be retried safely using its stored receipt.

To reject a settled candidate, call `POST /memory-organizations/{id}/candidate/discard`. Cancel an active run first through the run API. Failed and cancelled generation cannot publish. Apply requires current write permission and an enabled target Space. Discard and apply retain the independent candidate memory for inspection; delete it separately when no longer needed. Removing the target or candidate makes publication unavailable. Publication changes file content, not target guidance or Session bindings.

### Write from current knowledge

Agents and organizers track the file versions their conversation has read. Each file mutation checks those observations within the same memory, atomically with the write. A changed source or target returns `read_conflict` and names the file to reread.

To continue, the agent reads that file with `memory_file_view`, considers the new content in its next response, then writes again. Harness supplies the version conditions. A write issued alongside the reread still uses the old observations. Observations persist with the conversation checkpoint. Standalone file memories use the same workflow.

## Standalone memories

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span>

- [Standalone memory reference](standalone-memory.md)

## Inspect memories loaded for a reply

To show which memories were automatically loaded, save `memory_sources` with the reply. The thread SSE stream supplies it in `delta.item.memory_sources` with `TEXT_MESSAGE_END`; run items expose it in `content.memory_sources`. A stream boundary confirms checkpoint durability; message end alone does not.

Each entry identifies the mount (`memory`) and resource (`memory_id`). Record entries contain recalled IDs and SHA-256 text digests. File entries include delivery status and the paths, digests, byte counts and truncation flags of content actually loaded. Index entries alone do not mean file content was read. `sources_truncated` marks an incomplete reference list.

Use this as evidence of automatic delivery, not proof that the model used a particular fact. It excludes inherited conversation history and explicit tool reads. A missing field means sources were not captured; interpret empty lists with their `status`. Old replies are not backfilled.

History returns saved sources without querying the provider again. Later memory edits or deletion leave that evidence unchanged. Digests cannot reconstruct earlier text; viewing current content still needs an authorized memory API request and an available resource.
