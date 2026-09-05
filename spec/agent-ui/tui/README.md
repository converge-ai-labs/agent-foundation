# Agent UI TUI Specifications

## Overview

The Agent UI TUI is a conversation-first terminal interface distributed with `a13n-ui`. Textual and one `AgentUiApp` share a process. One conversation shows the current root and its descendants; a transient Thread picker finds another conversation. There is no Workbench or daemon mode. [Setup and Environment Readiness](../06-setup-and-environment-readiness.md) owns explicit first-use initialization and selected Sandbox recovery.

## Document Catalog

| Document                                           | Owning contract                                                                                                                         |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                   | Product boundary, conversation and picker information architecture, responsive layouts, startup, and end-to-end terminal flows          |
| [01-interaction-model.md](01-interaction-model.md) | Semantic timeline, composer, commands, decisions, tools, child work, activity indicators, navigation, and control semantics             |
| [02-textual-runtime.md](02-textual-runtime.md)     | Textual architecture, App integration, state reduction, streaming, reconciliation, resource discipline, failure behavior, and lifecycle |

## Reading Path

Read [Agent UI Overview](../00-overview.md), [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md), then `00`, `01`, and `02` in this directory. Read [Configuration Sources and Resources](../01-configuration-and-resource-catalog.md) and [Extension and Capability Discovery](../01a-extension-discovery-and-management.md) when implementing catalog inspection or selectors, [Local Storage and Recovery](../03-local-storage-and-recovery.md) when implementing retained history or restart behavior, and [Projects, Threads, and Environments](../04-projects-threads-and-environments.md) when implementing sticky selections or Project-aware references.

## Authority Rules

01. `AgentUiApp` remains the only local application, execution, storage, and live-presentation authority.
02. The TUI consumes only detached App commands, queries, receipts, projections, and live events. It never opens Agent UI storage or reconstructs native Harness state.
03. Focus identifies the Thread currently rendered in detail; it does not acquire or transfer execution ownership.
04. The transient Thread picker navigates bounded App summaries; it has no second composer or cross-row execution controls.
05. Live events provide provisional low-latency presentation. Selected continuations, terminal operation views, and retained projections provide closed truth.
06. Ordinary input submitted while a root Run is active is an exact steering attempt. The TUI owns no hidden next-turn queue.
07. Deferred interaction submits one complete response batch against the exact selected continuation. UI sequencing never weakens that correlation.
08. Responsive layout changes visibility and placement only. It never changes Thread, Run, configuration, continuation, or authorization semantics.
09. Only the focused Thread owns a full mounted timeline and detailed live subscription. Picker rows and inactive Threads remain lightweight projections.
10. The TUI uses an explicit accepted `--project` launch override when supplied, otherwise resolves one launch Project from the current directory, and defaults the picker to that Project filter with an explicit All Projects fallback.
11. Outside the explicit setup publication workflow, the TUI can patch only supported non-Project sticky selections. It can explicitly reference a Skill already present in an App-supplied effective catalog, but it does not create or select Projects, edit roots, or create, edit, delete, import, install, or upgrade desired resources, Skills, Capabilities, or extension packages.
12. Exiting the TUI does not imply that root or child work continues elsewhere. The owning App performs bounded drain and cancellation without inventing completion.

## Conventions

- A **decision** is a pending approval or external response represented by a selected suspended root continuation.
- **Attention** means that a bounded current fact should be surfaced before ordinary running or idle work. It does not imply notification delivery or durability.
- A **launch Project** is the accepted configured Project selected explicitly for this TUI invocation or resolved from its initial current directory. It initializes new drafts and the default picker filter; it is not a root reorder or override of an existing Thread. First-use setup can create a Project only after explicit confirmation.
- A **Project filter** in the TUI is either the launch Project ID or no ID for All Projects. It is terminal-local selection and never changes a Thread's stored Project.
- A **draft Thread** is TUI-local creation state under the launch Project before `AgentUiApp.create_thread()` succeeds. It is not a persisted Thread or accepted input.
- A **Skill reference** is an exact TUI composer selection rendered as `$<skill-name>` and resolved through an App-supplied effective Skill catalog. It is an explicit request to use an existing Skill, not Skill source mutation, eager content attachment, or new authority.
- A **timeline block** is bounded terminal presentation state derived from detached retained or live values. It is not Harness state or a persistence format.
- A **review** is a presentation of App-supplied safe data. Viewing or selecting it grants no execution authority.
