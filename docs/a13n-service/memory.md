---
title: Memory
description: Maintain long-term team knowledge across conversations, run automated Dreams-style distillation workflows, and review candidate diffs before publishing.
---

In multi-agent systems, important user preferences, architectural decisions, and operational guidelines should outlive single conversations. **Service Memory Spaces** provide versioned, structured file storage that agents can consult and update across sessions.

Service pairs this durable storage with an automated **Organization Engine** (inspired by cognitive "Dreams" consolidation), extracting verified facts from past dialogues, resolving conflicting information, and staging changes as reviewable candidates before atomically merging them into production memory.

| Organizational need | Recommended memory pattern |
| :--- | :--- |
| **Shared team knowledge & session notes** | [Memory Space](#memory-spaces) |
| **Standalone versioned file trees for specific threads** | [Standalone file memory](standalone-memory.md#create-a-file-memory) |
| **Semantic vector retrieval of short facts** | [Record memory via Mem0](standalone-memory.md#record-memories) |

## Memory Spaces

A **Memory Space** organizes persistent information into a clean filesystem structure:

- `global/`: Contains enterprise guidelines, shared codebase documentation, and team preferences available to all sessions.
- `global/MEMORY.md`: The primary knowledge index loaded directly into agent prompt contexts.
- `sessions/{session_id}/`: Dedicated directories where agents maintain session-specific notes and project handoffs.

### Select Spaces

1. In Console, navigate to **Memories → Spaces** and click **Create Space** (e.g., named `Engineering Guidelines`).
2. Open your target agent's settings, enable Memory participation, and mount the Space under a mount name such as `team_kb` with `write` access.
3. Start a test session with the agent and ask:
   > *"Record our team's database convention: all new tables must include created_at and updated_at UTC timestamps in global/conventions.md."*
4. Open the Space's **Files** tab to review the newly committed Markdown file, complete with author attribution and revision timestamps.
5. Start a separate, clean session with another agent mounting the same Space. Ask: *"What are our team's database timestamp conventions?"* The agent consults the memory space and returns the accurate rule.

Through the API, create a Space using `POST /api/v1/memory-spaces`:

```json
{
  "name": "Engineering Guidelines"
}
```

Attach the Space to an agent revision or active session by providing its ID:

```json
{
  "memory": {
    "enabled": true,
    "spaces": [
      {
        "name": "team_kb",
        "space_id": "mspace_abc123",
        "access": "write"
      }
    ]
  }
}
```

### Organize saved knowledge

As agents interact with users, individual notes and working documents can accumulate unstructured data. The **Organizer Engine** runs periodic background sweeps to consolidate documents, reconcile duplicates, and update the central `global/MEMORY.md` index.

To trigger an organization cycle manually:

1. In Console, navigate to **Memories → Spaces → Organization**.
2. Select an organizer reasoning model and click **Organize now**.
3. The organizer spawns a dedicated, isolated execution run that scans files, deduplicates overlapping records, and produces a structured knowledge graph.

To automate maintenance, enable an **Automatic organization policy** in your Space configuration:

```json
{
  "organization": {
    "enabled": true,
    "model": "model-gemini-pro",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {
      "type": "change_count",
      "count": 10
    }
  }
}
```

In count-based mode, the organizer executes whenever team members or agents accumulate 10 saved file modifications, observing a 15-minute cooldown between rounds to optimize token costs.

### Automatically extract conversation knowledge

Beyond reviewing explicitly saved memory files, the organizer can automatically mine sealed conversation sessions for implicit business decisions:

```json
{
  "instructions": "Extract verified architecture decisions, approved API schemas, and production deployment policies.",
  "history": {
    "session_ids": ["sess_backend_refactor", "sess_auth_redesign"],
    "after": "2026-11-01T00:00:00Z",
    "max_runs": 100
  }
}
```

The engine inspects raw conversation transcripts within the designated time window, extracts grounded conclusions, and drafts updates for the knowledge base.

### Run a custom organization task

When you need an ad-hoc distillation over specific topics (such as preparing an onboarding guide before a major release), submit a custom organization task:

```json
POST /api/v1/memory-organizations
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "instructions": "Consolidate all Q4 database migration decisions and highlight any unaddressed schema migrations."
}
```

The system provisions a tracked background run and records all token usage and trace logs under the organization audit view.

### Extract knowledge from selected conversations

Targeted distillation allows you to specify up to 100 session IDs as training ground:

```json
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "instructions": "Extract approved product roadmap features and capture open technical debates.",
  "history": {
    "session_ids": ["ses_feature_planning", "ses_sprint_review"],
    "after": "2026-10-01T00:00:00Z",
    "before": "2026-11-01T00:00:00Z",
    "max_runs": 50
  }
}
```

The model treats human statements as authoritative source evidence, distinguishes user corrections from unconfirmed agent guesses, and retains unresolved debates explicitly.

### Review a candidate before publishing

To protect enterprise knowledge bases from hallucinated or unverified modifications, custom organization tasks default to staging changes in an isolated **Candidate Tree**:

```json
{
  "output": "candidate"
}
```

When set to `"candidate"`, the organizer performs all file edits on an ephemeral clone of the target space. Live production documents remain completely untouched.

1. Once generation finishes, open **Review candidate** in Console.
2. The interactive review portal displays changed file counts, addition/deletion metrics, and a full file tree explorer.
3. Select any file to open a visual side-by-side **Diff view** comparing the production baseline against the generated candidate.
4. Mark files as reviewed as you inspect changes.
5. Click **Apply all changes** to merge the candidate tree atomically into the live Memory Space.

If concurrent edits occurred on the live space during generation, Service flags the baseline conflict (`baseline_changed`), allowing you to inspect the newer changes or select `replace_all` after deliberate review.

### Compose a Dreams-style workflow

A complete Dreams-inspired workflow establishes a continuous virtuous cycle:

1. **Daily interaction:** Developers and agents converse normally across different sessions. Agents store immediate operational notes in their session directories.
2. **Scheduled distillation:** An automatic policy triggers during off-hours, scanning recently sealed sessions.
3. **Structured synthesis:** The organizer extracts enduring patterns into `global/`, archives obsolete notes, and compiles an updated `global/MEMORY.md`.
4. **Candidate gate:** Team administrators review the staged candidate diff in Console and approve publication.
5. **Enriched context:** All subsequent agent sessions immediately inherit the refined corporate knowledge base.

### Write from current knowledge

To prevent race conditions when multiple concurrent agents update the same memory file, Service implements optimistic concurrency control:

- When an agent reads a memory file, Harness records its observed version.
- When the agent issues a write, Service verifies that the underlying file version matches the observation.
- If another session modified the document in the interim, the write fails with `read_conflict`.
- The agent transparently re-reads the updated file via `memory_file_view`, incorporates the latest content, and retries the mutation.

## Standalone memories

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span>

For applications that need to bind isolated, ephemeral file trees or specialized semantic vector stores directly to individual conversation threads, explore the [Standalone Memory Reference](standalone-memory.md).

## Inspect memories loaded for a reply

To audit which memories were injected into a model's context for a specific reply:

- In Console, expand the message's **Debug info** panel.
- In API streams, inspect the `memory_sources` metadata field delivered on the `TEXT_MESSAGE_END` event.

Each entry identifies the exact Space ID, mounted path, content digest, and character count injected into the model prompt, providing full audit compliance.
