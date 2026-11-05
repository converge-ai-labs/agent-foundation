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
