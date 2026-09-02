# Agent Management

Foundation exposes `Agent` as the stable Workspace-owned identity and `AgentRevision` as its immutable executable configuration. There is no `AgentPreset` type and no mutable draft embedded in the Agent head.

## Resource model

```python
class Agent:
    id: str
    organization_id: str
    workspace_id: str
    source: Literal["builtin", "custom"]
    name: str
    description: str | None
    version: int
    current_revision_id: str
    enabled: bool
    archived_at: datetime | None
    duplicated_from_agent_id: str | None
    duplicated_from_revision_id: str | None
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

class AgentRevision:
    id: str
    organization_id: str
    workspace_id: str
    agent_id: str
    version: int
    config: AgentConfig
    config_digest: str
    resolved_model: ResolvedModel
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    runtime_lock_digest: str
    resolved_skills: tuple[ResolvedSkill, ...]
    resolved_environment: ResolvedEnvironment | None
    resolved_subagents: tuple[ResolvedSubagent, ...]
    content_digest: str
    source_revision_id: str | None
    created_by: PrincipalRef
    created_at: datetime
```

`Agent.version` and the current `AgentRevision.version` are always equal. Version starts at `1` and advances only when a genuinely new immutable Revision becomes current. Metadata, enabled state, and archival changes do not advance it.

## Creation and revision semantics

Create Agent accepts `name`, optional `description`, and one complete `config`. Foundation authorizes and resolves every referenced dependency, then atomically creates the Agent and Revision v1. It never exposes an Agent without a current Revision.

Create Revision accepts `expected_version` and a complete replacement `config`:

1. authorize the operation;
2. verify the expected Agent version;
3. resolve exact Model, Plugin, Skill, Environment, and subagent revisions;
4. canonicalize the complete frozen content and compute its digest;
5. return the current Agent and Revision unchanged for a semantic no-op;
6. otherwise create immutable version `current + 1` and advance the head in the same transaction.

Revision rows are append-only. A rollback is `Restore Revision`: Foundation revalidates the retained dependency graph and copies the selected historical content into a new later Revision. It never moves the head backward or repoints it to an older row. `source_revision_id` records the restored source.

Duplicate revalidates the exact current Revision and creates a new Agent with its own v1 Revision. The new head records the source Agent and Revision IDs.

## Exact dependency freezing

An Agent config references `model_revision_id`, exact PluginVersion IDs in on-demand mode, exact SkillRevision IDs, and an exact EnvironmentRevision ID. Subagents may select an exact AgentRevision ID or Agent version; resolution stores the exact selected Revision ID. Invocation never follows a mutable dependency head after acceptance.

The complete transitive subagent graph is resolved before commit. Cycles, missing revisions, disabled or archived dependencies, incompatible Plugin runtime locks, and changed prepare/commit evidence fail closed.

## Invocation

Invocation accepts an Agent ID and optional exact AgentRevision ID. Omission selects `current_revision_id`. Acceptance finishes authorization and initial reads in a short session, resolves run-scoped overrides, then rechecks exact evidence in the commit transaction.

Historical Agent Revisions remain invocable when the Agent is enabled and not archived, even when a retained Plugin or other catalog entry was later archived. The immutable artifact and evidence must still exist and match. A changed digest or missing runtime lock makes the Revision non-executable.

## Metadata and lifecycle

`name` and `description` are mutable head metadata. `enabled` and `archived_at` are independent lifecycle axes:

- Disable sets `enabled=false`.
- Enable revalidates the current Revision and sets `enabled=true`.
- Archive requires the Agent not to be enabled and sets `archived_at`.
- Unarchive clears `archived_at` without enabling the Agent.

GET returns a strong `ETag`. Metadata and lifecycle mutations require exact strong `If-Match`; weak validators and `*` are rejected. These mutations never change `version`.

## Persistence

### `agents`

The stable head stores identity, tenancy, `name`, description, `version`, `current_revision_id`, lifecycle axes, duplication provenance, actors, and timestamps. `(workspace_id, normalized_name)` is unique.

### `agent_revisions`

The immutable table stores complete config, frozen resolution, digests, provenance, actor, and time. `(agent_id, version)` is unique. The head and current Revision are updated atomically.

Runs and downstream records store `agent_revision_id`, not only Agent ID or version.

## API

- `POST /api/v1/workspaces/{workspace_id}/agents`
- `GET /api/v1/workspaces/{workspace_id}/agents`
- `GET /api/v1/agents/{agent_id}`
- `PATCH /api/v1/agents/{agent_id}`
- `POST /api/v1/agents/{agent_id}/revisions`
- `GET /api/v1/agents/{agent_id}/revisions`
- `GET /api/v1/agent-revisions/{revision_id}`
- `POST /api/v1/agents/{agent_id}/revisions/{revision_id}/restore`
- `POST /api/v1/agents/{agent_id}/duplicate`
- `POST /api/v1/agents/{agent_id}/{enable|disable|archive|unarchive}`

Create, Create Revision, Restore, Duplicate, and lifecycle commands require `Idempotency-Key`. Version conflicts use `agent_version_conflict`; representation conflicts use `etag_mismatch`.

## Authorization and audit

Agent actions are authorized against the stable Agent identity. Role bindings use `resource_type="agent"`. Every successful mutation records the exact Agent and Revision references in security audit and outbox evidence without secret values or resolved credentials.
