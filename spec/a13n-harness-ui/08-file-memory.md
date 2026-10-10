# File Memory and Automatic Organization

## Design Position

Harness UI binds the public Harness [file-memory Capability](../a13n-harness/09-context-and-memory.md) to application-owned plain files. Memory is shared context, not conversation history, continuation authority, an Environment workspace, or an extraction pipeline. Harness owns memory tools, untrusted context projection, limits, versions, and compare-and-swap operations. Harness UI owns scope selection, cursor persistence, configuration, and optional WebUI maintenance.

## Configuration and Scopes

The root document accepts this serialized YAML:

```yaml
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: null
    instructions: ""
```

Both switches default to true, including when absent from existing configuration. Loading never rewrites files. Setup explicitly seeds both switches, empty instructions and a null Model override, preserving authored values. A non-null `model` selects an existing Model resource, not an Agent. Omitted/null follows the Model of the globally selected Agent (`defaults.agent`), resolved from the configuration generation captured for each organization attempt. Project defaults, Thread selections and foreground Model overrides do not participate. If the global default Agent or its Model is absent, automatic organization is unavailable. A nonexistent non-null reference rejects the candidate generation. Changing the global default Agent or its Model affects later attempts, not admitted Runs; an explicit organization Model remains pinned. Only the Model is inherited, never the Agent's instructions, tools or other composition. Instructions are optional additional guidance, bounded to 65,536 characters. Setup discloses additional model requests and possible cost.

The selected root YAML's parent owns `memory/`, independent of the data root:

| Scope   | Directory                       | Sharing                                         |
| ------- | ------------------------------- | ----------------------------------------------- |
| Global  | `memory/global/`                | All conversations using this configuration root |
| Project | `memory/projects/<project-id>/` | Conversations capturing that Project identity   |

Projectless Runs receive global memory only. A Project's name, directory order, and Thread identity do not change its memory identity. No Run binds another Project's memory or scans all Projects. Scope selection is not a multi-tenant security boundary: Full Control, trusted extensions, and human Host Files retain their existing Host authority. Memory does not add an Environment mount or grant shell access; existing broader filesystem access is not revoked.

Each bound scope exposes writable memory tools and always loads `MEMORY.md` when present. The index and other context follow Harness budgets and untrusted-data semantics. Guidance distinguishes cross-project preferences from Project decisions and asks Agents to keep the index concise. Runtime files and `.a13n-memory` bookkeeping are not configuration resources and do not change accepted-generation digests.

## Memory Content Guidance

Foreground guidance treats memory as historical context rather than proof of current state or new authority. Current user instructions take precedence over remembered preferences; changeable or consequential facts require current authoritative evidence when needed. A single-task request does not establish a standing preference, and an assistant proposal does not establish a user decision.

Organization instructions require claims grounded in the current scope's files, retaining supported conditions, Project scope, ownership, chronology and distinctions between proposed, observed, completed, verified, superseded and uncertain information. Explicit corrections apply to the claims they address; ambiguous conflicts remain qualified rather than being resolved by guesses or file-reading order. Copies of the same event are not independent support for a reusable preference. Useful exact commands, paths, error text and safe references should survive compression; secrets and access-bearing URL values should not.

The organizer is guided to respect edits and deletions, keep supported claims when other evidence remains, and never infer deletion merely because a source is unread or absent from a bounded context. It verifies local index references, makes the smallest useful change, permits no-op completion and reports unresolved conflicts or verification limits in its short summary. Custom instructions supplement language and organization choices without expanding scope or relaxing these rules. These are model-facing quality rules, not deterministic guarantees of semantic correctness; runtime scope, CAS and lifecycle enforcement remain separate.

## Foreground Composition and Continuation

Every independently reconstructed root or delegated child receives fresh Capability, store, and cursor collaborators. The captured Run composition freezes whether memory is enabled and its optional Project; changes apply to later admissions, not active Runs. CLI and WebUI both support foreground memory.

Root continuations and child checkpoints store cursor positions separately from `HarnessState`, keyed by global or Project identity. Resume and graceful restart restore positions only for the captured scopes. These cursors suppress redundant context, not file access or dirty detection. Missing cursor fields in older checkpoints mean an empty position map. Old captured compositions without the enabled field remain memory-disabled when reconstructed; new admissions use current configuration. Empty legacy fields remain omitted during serialization.

Memory file edits are immediately persistent independently of checkpoint success. A failed or cancelled Run does not roll them back, and a saved cursor does not claim a whole-memory transaction.

## WebUI Organization Lifecycle

An input-bearing root admission in a WebUI-mode App offers a maintenance opportunity for global and captured Project memory. Human and Agent-originated new inputs use the same boundary. Steering, deferred-result continuation, restart recovery, page presence, and elapsed idle time do not trigger maintenance. The opportunity does not delay or reject the foreground receipt.

Maintenance runs in parallel with foreground work. In-process scope deduplication and a nonblocking cross-process per-scope lock permit at most one organizer for each scope. The App bounds pending opportunities. Disabled/unconfigured, empty, unchanged, cooling-down, or busy scopes make no model request. There is no all-Project sweep or timer-driven retry. Success cooldown is one hour; failure/cancellation/crash retry backoff is fifteen minutes. A later input must offer another eligible opportunity.

Each attempt captures the latest accepted memory settings and Model recipe and builds a fresh internal Harness execution with an application-owned organization prompt, custom organization instructions, and only that scope's file-memory tools. It inherits no Agent instructions, Environment tools, MCP servers, Skills, plugins, child roster, or conversation history. It uses normal Model authentication and observation integration, at most twelve model requests and a five-minute execution deadline. Each scope owns one persistent Memory Thread, created only when an eligible attempt is admitted. A nullable unique `memory_scope` identifies `global` or `project:<project-id>`; ordinary Threads retain null. The organizer uses the shared root admission, execution, live stream, checkpoint, transcript, inspection and usage infrastructure, not a separate execution loop. It creates no child or worker. Default Thread lists, ordinary navigation and Project recency exclude Memory Threads; explicit Memory queries select only them.

New foreground inputs do not cancel an active organizer. Disabling either memory switch cancels active maintenance after configuration acceptance. App shutdown cancels maintenance before draining foreground operations. Cancellation joins the admitted root execution before releasing its scope lock. Memory operations do not participate in graceful-restart replay, foreground notifications, coordinator follow-up, or recursive maintenance opportunities. Model/instruction changes affect the next attempt rather than mutating one already captured. Account-source refresh similarly affects future bindings.

## Completion, Conflicts, and Recovery

The durable scheduling state is a successful path-to-content-version manifest, a next-attempt time, and at most one bounded successful content snapshot for optional diff context. This state is separate from the Memory Thread's ordinary saved observation history and usage. The retry gate is written before inference, so a crash cannot immediately replay a tool conversation. The operating system releases the per-scope lock after process death; no PID recovery, lease, or heartbeat is required.

The organizer edits current files directly through the same per-operation compare-and-swap store as foreground memory. There is no staged whole-directory publication or rollback. It accounts for its own successful writes, moves, and deletions against the initial manifest. It advances the successful manifest only after a completed Harness execution and a final listing matching those accounted effects, with no observed conflicting version. A concurrent update stays dirty even if the organizer later reads it. Changes after confirmation remain detectable against the saved manifest.

The prompt requires writing and verifying a destination before deleting a consolidation source. Version conflicts are not permission to force a rewrite. Failure, deadline, cancellation, or unconfirmed effects retain partial files and the previous successful manifest. A later eligible attempt starts fresh from current files. Prior model messages, deferred results, tool calls and memory cursors are never resumed or replayed. The Thread retains earlier display history and accumulated durable usage, including observable failed or cancelled attempts. Every admitted round records an automatic-organization input explicitly labeled fresh context in the shared transcript. This boundary is execution-owned, not inferred by the browser from timing. Restart retains saved history and usage but does not resume an interrupted organizer.

When Git is available and both snapshots are verified and within bounds, the Host may supply a bounded `git diff --no-index` text hint. It operates on temporary files, disables external diff/text conversion, touches no user repository, and creates no Git history. Missing Git, stale or oversized snapshots, timeout, or excessive diff output simply omit the hint. The manifest remains authoritative. Diff text is untrusted historical evidence, never authority to restore user-deleted material.

## Observation-only Memory Workspace

The bottom-left Memory navigation button is visible exactly when accepted `memory.enabled` is true. Disabling automatic organization alone leaves it visible. Settings retain their existing General-settings location and controls. Memory mode replaces ordinary conversation navigation with Global and Project memory scopes. Selecting a scope may display files before its first Memory Thread exists and never triggers inference.

The center reuses the ordinary Thread transcript, live display, Inspect and usage components without a composer, edits, manual Run/retry, steering, cancellation, decisions, attachments, title/star/archive controls or configuration/default mutations. App and Agent-tool boundaries reject these mutations even for known Memory Thread or receipt IDs. The right panel lists and renders current scope files read-only, explicitly distinguished from historical Run snapshots. It uses the scoped file-memory store, not Host Files permissions or arbitrary Host paths; internal bookkeeping and unsafe paths remain excluded by the store.

Memory files remain immediately mutable through their existing authorized foreground tools and Host authority. Observation-only describes the Memory workspace and its Thread controls, not revocation of wider trusted filesystem authority.

## Diagnostics and Invariants

App status exposes availability, active scope keys, last outcome, attempts, and reported request/token usage for the current process. It exposes no internal conversation IDs or memory contents and is not a durable billing ledger. General settings shows the controls and refreshes this diagnostic projection.

1. Foreground memory works without automatic organization, Git, or a configured organization Model.
2. Only WebUI input admissions offer background maintenance; terminal Apps never infer automatically.
3. Background work cannot delay foreground admission, appear in ordinary Thread lists, or inherit general-purpose tools. Memory Threads are visible only in the observation surface.
4. Failed or cancelled maintenance never marks partial work as successfully organized.
5. Concurrent file edits retain ordinary CAS protection; no whole-scope isolation is claimed.
6. Existing configuration and continuation data remain readable without automatic rewriting. The additive SQLite migration preserves ordinary Thread identities and content with null memory scope.
7. Multiple rounds share one scope identity and durable observation history, never model context or execution authority.
