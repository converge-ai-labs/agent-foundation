# Projects, Threads, and Environments

## Design Position

A Project is the only Agent UI concept for grouping local roots. It is a mutable named ordered root list modeled after Codex Project. There is no separate Workspace resource, Workspace revision, or `WorkspaceBinding` input.

A Thread is one continuation-backed root or child conversation. It owns mutable sticky selections for Project, Agent, Environment profile, Harness Plugins, Environment Run Extensions, and MCP servers. A Run can atomically patch those selections and captures their complete effective values before execution. Subsequent changes do not affect the admitted Run.

## Projects

One file under `projects/` defines a Project:

```yaml
schema_version: "1"
kind: project
id: project-agent-foundation
name: Agent Foundation
position: 0
roots:
  - path: /work/agent-foundation
  - path: /work/design-notes
```

The conceptual model is:

```python
class ProjectRoot(BaseModel):
    path: str


class Project(BaseModel):
    id: ProjectId
    name: str
    roots: tuple[ProjectRoot, ...]
    position: int
```

Roots are canonical absolute existing directories, ordered and unique. The first root is the default working directory and `workspace` mount. Later roots become `workspace-2`, `workspace-3`, and so on. Project position provides stable user ordering; recency can be computed from associated non-archived Threads and does not belong in the file.

Changing Project roots affects later Runs of every Thread selecting the Project. A Run already admitted retains its captured roots. Removing a Project file removes it from the next accepted generation. Existing Threads retain the unresolved ID and reject later Runs until explicitly reassigned; no global fallback silently changes their local authority.

## Thread Identity

```python
class Thread(BaseModel):
    thread_id: ThreadId
    parent_thread_id: ThreadId | None
    created_at: datetime
    updated_at: datetime
    title: str | None
    configuration: ThreadConfiguration
    initial_state: ObjectRef
    continuation: ContinuationRef | None
```

A root Thread has no parent. An async child records its immediate parent and shares no mutable runtime object with it. The selected `HarnessState.thread_id` equals the Agent UI Thread ID; Agent UI does not add a second Session identity for the same conversation.

Current Run status, subscribers, steering queues, and live output remain process-local. A Thread can exist with no live Run. Thread creation calls `HarnessState.new()` once, uses its generated `thread_id` as the Agent UI Thread ID, and publishes that empty value as `initial_state`. The Thread initially has no selected continuation. Its first admitted Run uses `initial_state`; only a completed or suspended Run boundary can publish and select the first continuation.

## Sticky Thread Configuration

```python
class AgentResourceSource(BaseModel):
    agent: AgentId


class MarkdownSubagentSource(BaseModel):
    markdown: SubagentId


AgentSource = AgentResourceSource | MarkdownSubagentSource


class ThreadConfiguration(BaseModel):
    version: int
    project_id: ProjectId
    agent_source: AgentSource
    environment_profile_id: EnvironmentProfileId
    harness_plugin_ids: tuple[PluginId, ...]
    environment_run_extension_ids: tuple[RunExtensionId, ...]
    mcp_server_ids: tuple[McpServerId, ...]
```

The stored value is exact. It contains no `inherit`, omitted, or globally enabled state.

A new root Thread resolves an explicit Agent resource or the root YAML Agent default into `AgentResourceSource`, then resolves the other creation defaults and stores the exact result. Root Threads cannot select a Markdown subagent as their source; that concise format depends on a parent Agent capture.

A child Thread stores the selected roster entry as either an Agent resource or Markdown subagent source. Project, Environment profile, and Run Extensions default from the admitting parent capture. Agent-resource children use their own Plugin and MCP defaults when present; Markdown children inherit the admitting parent capture's exact Plugin and MCP lists. After creation the child owns these stored selections independently.

## Configuration Patch and Run Admission

A Thread can change configuration before or after any Run:

```python
class ThreadConfigurationPatch(BaseModel):
    project_id: ProjectId | Unset
    agent_source: AgentSource | Unset
    environment_profile_id: EnvironmentProfileId | Unset
    harness_plugin_ids: tuple[PluginId, ...] | Unset
    environment_run_extension_ids: tuple[RunExtensionId, ...] | Unset
    mcp_server_ids: tuple[McpServerId, ...] | Unset
```

Omitted fields retain the current Thread value. An empty collection disables all resources on that axis. A non-empty collection is the exact ordered enabled set. There are no retained disabled association rows.

A configuration mutation command contains an exact expected version:

```python
class ThreadConfigurationMutation(BaseModel):
    expected_version: int
    patch: ThreadConfigurationPatch
```

Every non-empty patch, including one admitted with Run input, requires `expected_version`. A root Thread patch rejects a Markdown `agent_source`; a child Thread can replace its source with either form. A Run admission with no configuration patch can omit the expected version because it does not write the configuration head. The combined admission operation:

1. loads the current Thread configuration;
2. rejects a non-empty patch whose required expected version differs;
3. applies and validates the patch;
4. commits the new exact configuration and incremented version;
5. captures the accepted file generation and current Project roots;
6. resolves and publishes the immutable Run composition;
7. closes all transactions before native construction or external I/O;
8. starts the Harness Run with the selected continuation's `HarnessState`, or the Thread's immutable `initial_state` when no continuation is selected.

The accepted patch applies to this Run and subsequent Runs. A separate update during an active Run is allowed but affects only the next admission. Steering never changes captured configuration.

## Environment Profile and Binding

A Thread selects exactly one Environment profile defined by [Extension Discovery and Management](01a-extension-discovery-and-management.md#environment-provider-discovery-and-profile-resources). A profile chooses one installed Provider plus Agent UI Host adapter configuration; it does not represent the runtime `Environment.environment_id`. Native is the omission fallback only while creating a root Thread; a later missing or failed explicit profile never falls back.

For each captured Project root, the App:

1. resolves the exact Provider and approved Host adapter;
2. loads current Host-authoritative state under the complete binding key;
3. asks the adapter to materialize root-specific validated Provider configuration;
4. creates a fresh pre-entry-inert `Environment` adapter;
5. constructs the deterministic Harness mount set;
6. creates fresh selected Environment Run Extensions around that aggregate.

The Provider configuration and adapter do not own the Project root list. The adapter receives one root at a time and can reject roots it cannot represent.

## Host-authoritative Environment State

Native and Local EIP use Project roots directly and ordinarily retain no portable re-entry state. A stateful Provider can return `EnvironmentState` for one root.

Agent UI uses one private binding identity:

```text
Thread ID
+ Environment profile ID and normalized profile digest
+ Host adapter key
+ normalized Project root path
```

The profile digest reuses the accepted generation's canonical normalized content for `provider_key`, Provider schema version, Provider configuration, Host adapter key, and adapter configuration. It excludes filename, YAML formatting, comments, and display-only fields. State lookup, supplied-state comparison, and publication use exactly that identity. Existing authoritative `None` does not permit fallback from `HarnessState.environment_states`. Continuation Environment state is a portable observation only and can be adopted only through an explicit import boundary.

Changing the selected Environment profile or any behavior-affecting normalized content produces a different state identity. Presentation-only edits preserve it. Switching back to the same compatible identity can recover its previous state. Removing and later restoring the same Project root behaves similarly. State is never shared merely because two Threads select the same Project.

## Root Run Environment Flow

```mermaid
sequenceDiagram
    participant Caller
    participant App
    participant Store
    participant Project
    participant Provider
    participant Harness

    Caller->>App: input plus optional Thread configuration patch
    App->>Store: apply patch and load prior continuation
    App->>Project: capture current ordered roots
    App->>Provider: load state and create fresh adapters
    App->>Harness: Run with captured mounts and extensions
    Harness-->>App: result and HarnessState
    App->>Provider: close and read final cached state
    App->>Store: compare-and-select changed Environment state
    App->>Store: publish and select continuation
```

Cleanup precedes final state reading. Environment-state publication occurs after failure or cancellation when a changed final value is known. Equal state performs no write. Environment-state and continuation publication are independent facts and do not roll one another back.

## Async Child Environments

A newly delegated child Thread initializes Project and Environment profile selections from the parent Run capture. Every child segment later uses the child Thread's own current selections and can apply an explicit patch before resume.

Each segment receives fresh Provider runtime collaborators, adapters, and Environment Run Extensions. It never borrows the parent's entered Environment facade. A descendant initializes from its admitting child capture under the same rule.

## Local EIP Runtime

Agent UI releases select one exact `agent-envd` release manifest and target hashes. Local EIP resolves only that managed executable or one explicit validated local override. It does not search ambient `PATH` or download a binary for Native execution.

The executable cache carries no Thread, Project, root, or Environment authority. Daemons, transports, process handles, and output cursors remain process-local to fresh adapters and never enter `EnvironmentState` or Thread storage.

## Thread Tools and Project Authority

Model-visible root Thread tools can list and inspect Threads, start or continue another root Thread, and steer an active Run. `run_thread` rejects a child target, defaults to the root target's sticky configuration, and accepts no arbitrary local root paths from model arguments. Async child continuation uses the linked `resume_subagent` path so the current parent roster and delegation ceilings remain available. Changing Project requires an explicit authorized Thread configuration operation through the App boundary.

`steer_thread` targets an active Run and preserves that Run's captured composition. Same-active-Thread recursive run or steer calls are rejected.

## Failure Semantics

| Failure                                  | Outcome                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------- |
| Invalid or inaccessible Project root     | Candidate generation or Run capture fails before native execution           |
| Project removed from accepted generation | Existing Thread remains inspectable; next Run requires reassignment         |
| Stale Thread configuration version       | Patch and admission are rejected without partial changes                    |
| Provider or Host adapter missing         | Run capture fails; Native is not substituted                                |
| Current Environment state invalid        | Admission fails explicitly                                                  |
| Adapter entry or extension entry fails   | Harness reports Run failure; known changed cached state is still considered |
| Cleanup or state publication fails       | Failure is reported independently from continuation selection               |
| Continuation publication conflicts       | Prior or concurrent continuation remains current                            |

## Invariants

01. Project is the only local-root grouping concept.
02. Project roots are mutable, ordered, and captured per Run.
03. Thread configuration is mutable, exact, versioned, and sticky.
04. Omitted patch fields preserve prior Thread state; empty lists disable one complete axis.
05. A Run captures one configuration generation, one Thread version, and one Project root list.
06. Root and child Threads can change Agent, extension, MCP, Project, and Environment profile selections between Runs.
07. Every independent Run receives fresh Environment adapters and Run Extensions.
08. Environment state is isolated by Thread, Environment profile behavior, adapter, and root path.
09. Steering never changes an active Run's captured composition.
10. Destructive Provider lifecycle remains outside ordinary Run cleanup.
