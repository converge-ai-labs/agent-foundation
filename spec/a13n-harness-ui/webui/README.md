# Harness UI WebUI

## Overview

WebUI is the project-centric collaborative browser surface of Harness UI. Trusted participants connect to one foreground server, see one another's current page presence, and share conversations, prompt drafts, configuration, and explicitly enabled native Host access. It is not a multi-tenant service or a second Agent execution engine.

## Document Catalog

| Document                                                               | Owning contract                                                                                                                 |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                       | Browser product boundary, navigation, Project configuration experience, and application integration                             |
| [01-collaborative-conversations.md](01-collaborative-conversations.md) | Page/focus presence, editor presence, shared prompt CRDT, submission, and independent delivery boundaries                       |
| [02-host-computer-sharing.md](02-host-computer-sharing.md)             | Native Host files, Git-aware views, PTY, access gating, and human development workflows                                         |
| [03-distribution.md](03-distribution.md)                               | Bundled browser assets and the ready-to-use Python distribution                                                                 |
| [04-workbench-interaction.md](04-workbench-interaction.md)             | Default user journey, participant awareness, composer controls, configuration UX, code/diff workflows, and terminal interaction |
| [05-scoped-sharing.md](05-scoped-sharing.md)                           | Temporary Project/Thread grants, guest authority and the focused guest experience                                               |
| [06-design-guidelines.md](06-design-guidelines.md)                     | Harness visual language, density, type, alignment, navigation, conversation and overlays                                        |

## Reading Paths

Read `00` for the product, `04` for interaction and `06` for visual presentation. `06` specializes the [shared frontend foundation](../../frontend/design-system.md). Read `01` for page awareness and pair prompting, `02` for native server access, and `03` for packaging and container deployment boundaries.

[MCP Apps Host](../09-mcp-apps.md) owns inline tool-result Apps, retained originals, trusted interaction confirmations and separate-origin sandboxing.

[App and surfaces](../05-runtime-subagents-and-surfaces.md) owns listener startup, API-key selection, HTTP and realtime delivery, and execution authority. [Configuration](../01-configuration-and-resource-catalog.md), [Projects and Threads](../04-projects-threads-and-environments.md), and [storage](../03-local-storage-and-recovery.md) own the shared domain contracts consumed by the browser.

## Authority Rules

1. One `HarnessUiApp` owns execution and mutations; browser state holds view/draft state only.
2. A shared prompt draft is editable input, distinct from continuation checkpoints and accepted Runs.
3. Page presence reports where participants are looking without synchronizing navigation or establishing verified identities.
4. Host Files, Git views, and Host Terminal operate on the server machine independently of Agent Environments.
5. Native computer-sharing operations require explicit startup enablement, separate from API authentication bypass.
6. File-backed resources remain directly editable; the configuration UI writes to the same resource catalog.
7. Browser disconnect leaves Agent Runs and native terminals running; server process loss follows its own recovery contract.
8. Scoped sharing grants restrict guest browser/API access while prompt input uses the Thread's existing Agent execution authority.
9. Browser assets ship as part of the Harness UI Python distribution.
