# Agent UI TUI Specifications

## Overview

The Agent UI TUI is the terminal-native local workstation distributed with `a13n-ui`. It uses Textual in the same Python process as one `AgentUiApp` and exposes two complementary interaction modes:

- **Focus** operates one current root Thread through its conversation, live activity, decisions, child work, and sticky Thread selections.
- **Workbench** supervises bounded summaries under the launch Project filter or an explicit All Projects view, orders work by attention, and permits exact context-sensitive action without mounting every conversation.

The TUI is a presentation surface over the existing Agent UI domain. It does not own another Thread model, Agent loop, Environment runtime, storage path, durable queue, or process daemon. Closing its owning App lifetime ends process-local execution according to the Agent UI shutdown contract.

## Document Catalog

| Document                                           | Owning contract                                                                                                                         |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                   | Product boundary, Focus and Workbench information architecture, responsive layouts, startup, and end-to-end terminal flows              |
| [01-interaction-model.md](01-interaction-model.md) | Semantic timeline, composer, commands, decisions, tools, child work, attention ranking, navigation, and control semantics               |
| [02-textual-runtime.md](02-textual-runtime.md)     | Textual architecture, App integration, state reduction, streaming, reconciliation, resource discipline, failure behavior, and lifecycle |

## Reading Path

Read [Agent UI Overview](../00-overview.md), [Runtime, Async Subagents, and Surfaces](../05-runtime-subagents-and-surfaces.md), then `00`, `01`, and `02` in this directory. Read [Configuration Sources and Resources](../01-configuration-and-resource-catalog.md) and [Extension and Capability Discovery](../01a-extension-discovery-and-management.md) when implementing catalog inspection or selectors, [Local Storage and Recovery](../03-local-storage-and-recovery.md) when implementing retained history or restart behavior, and [Projects, Threads, and Environments](../04-projects-threads-and-environments.md) when implementing sticky selections or Project-aware references.

## Authority Rules

01. `AgentUiApp` remains the only local application, execution, storage, and live-presentation authority.
02. The TUI consumes only detached App commands, queries, receipts, projections, and live events. It never opens Agent UI storage or reconstructs native Harness state.
03. Focus identifies the Thread currently rendered in detail; it does not acquire or transfer execution ownership.
04. Workbench attention is a bounded presentation ranking over App facts and TUI-local acknowledgement state. It is not a durable lifecycle or scheduling fact.
05. Live events provide provisional low-latency presentation. Selected continuations, terminal operation views, and retained projections provide closed truth.
06. Ordinary input submitted while a root Run is active is an exact steering attempt. The TUI owns no hidden next-turn queue.
07. Deferred interaction submits one complete response batch against the exact selected continuation. UI sequencing never weakens that correlation.
08. Responsive layout changes visibility and placement only. It never changes Thread, Run, configuration, continuation, or authorization semantics.
09. Only the focused Thread owns a full mounted timeline and detailed live subscription. Workbench and inactive Threads remain lightweight projections.
10. The TUI uses an explicit accepted `--project` launch override when supplied, otherwise resolves one launch Project from the current directory, and defaults Workbench to that Project filter with an explicit All Projects fallback.
11. The TUI can patch only supported non-Project sticky selections. It does not create or select Projects, edit roots, or create, edit, delete, import, install, or upgrade desired resources, Skills, Capabilities, or extension packages.
12. Exiting the TUI does not imply that root or child work continues elsewhere. The owning App performs bounded drain and cancellation without inventing completion.

## Conventions

- A **decision** is a pending approval or external response represented by a selected suspended root continuation.
- **Attention** means that a bounded current fact should be surfaced before ordinary running or idle work. It does not imply notification delivery or durability.
- A **launch Project** is the accepted configured Project selected explicitly for this TUI invocation or resolved from its initial current directory. It initializes new drafts and the default Workbench filter; it is not a TUI-created Project, root reorder, file-default mutation, or override of an existing Thread.
- A **Project filter** in the TUI is either the launch Project ID or no ID for All Projects. It is terminal-local selection and never changes a Thread's stored Project.
- A **draft Thread** is TUI-local creation state under the launch Project before `AgentUiApp.create_thread()` succeeds. It is not a persisted Thread or accepted input.
- A **timeline block** is bounded terminal presentation state derived from detached retained or live values. It is not Harness state or a persistence format.
- A **review** is a presentation of App-supplied safe data. Viewing or selecting it grants no execution authority.
