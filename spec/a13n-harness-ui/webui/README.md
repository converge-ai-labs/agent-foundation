# Harness UI WebUI

## Overview

WebUI is the project-centric collaborative browser surface of Harness UI. Trusted participants connect to one foreground server, see one another's current page presence, and share conversations, prompt drafts, saved-output comments, configuration, and explicitly enabled native Host access. It is not a multi-tenant service or a second Agent execution engine.

## Document Catalog

| Document                                                               | Owning contract                                                                                                                         |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                       | Browser product boundary, navigation, Project configuration experience, and application integration                                     |
| [01-collaborative-conversations.md](01-collaborative-conversations.md) | Page/focus presence, editor presence, shared prompt CRDT, submission, and independent delivery boundaries                               |
| [02-host-computer-sharing.md](02-host-computer-sharing.md)             | Native Host files, Git-aware views, PTY, access gating, and human development workflows                                                 |
| [03-distribution.md](03-distribution.md)                               | Bundled browser assets and the ready-to-use Python distribution                                                                         |
| [04-workbench-interaction.md](04-workbench-interaction.md)             | Default user journey, participant awareness, composer/comment controls, configuration UX, code/diff workflows, and terminal interaction |
| [05-output-comments.md](05-output-comments.md)                         | Published comments on saved assistant text, immutable anchors, author attribution, publication/reconciliation, and execution separation |

## Reading Paths

Read `00` for the product and `04` for the end-to-end user experience. Read `01` for page awareness and pair prompting, `05` and [local comment storage](../03-local-storage-and-recovery.md#output-comment-storage) for persistent discussion, `02` for native server access, and `03` for packaging and container deployment boundaries.

[MCP Apps Host](../09-mcp-apps.md) owns inline tool-result Apps, retained originals, trusted interaction confirmations and separate-origin sandboxing.

[App and surfaces](../05-runtime-subagents-and-surfaces.md) owns listener startup, API-key selection, HTTP and realtime delivery, and execution authority. [Configuration](../01-configuration-and-resource-catalog.md), [Projects and Threads](../04-projects-threads-and-environments.md), and [storage](../03-local-storage-and-recovery.md) own the shared domain contracts consumed by the browser.

## Authority Rules

01. One `HarnessUiApp` owns execution and mutations; browser state is not another Thread or configuration authority.
02. A shared prompt draft is editable input, not a continuation checkpoint or durably accepted Run.
03. Participants share work and report current page presence; they do not synchronize navigation/scroll or establish verified account identities.
04. Host Files, Git views, and Host Terminal operate on the server machine, independently of the Agent's selected Environment.
05. Access to native computer-sharing operations requires explicit startup enablement; API authentication bypass alone does not enable them.
06. File-backed resources remain editable without the browser. Configuration UI does not create a parallel resource store.
07. Browser disconnect neither cancels an Agent Run nor destroys a native terminal. Server process loss has different recovery consequences from browser disconnect.
08. Browser assets remain part of the Harness UI distribution rather than an independently released application.
09. Published comments survive App restart in the existing local store; page presence and shared drafts do not.
10. Comments target saved AI output and do not modify it, enter model context automatically, or make the transcript a CRDT document.
