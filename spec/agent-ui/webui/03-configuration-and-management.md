# Configuration and Management

## Design Position

Configure makes the complete desired-resource tree manageable without replacing direct file editing. It exposes accepted resources, exact source text and digests, candidate diagnostics, installed catalogs, compatible account state, and desired-resource defaults through detached HTTP projections. Every browser mutation uses the same stable-read, full-generation validation, and no-clobber publication path as the CLI. Read-only App, listener, Run, and local-store inspection belongs to [Debugging and Diagnostics](04-debugging-and-diagnostics.md).

The browser maintains one source draft per open editor. Guided controls and raw YAML or Markdown editing are two views over that same draft, not two independently saveable models. A successful save publishes exact source bytes and selects a valid generation; no form directly updates SQLite or a normalized resource row.

## Management Areas

| Area        | Responsibilities                                                                                                  |
| ----------- | ----------------------------------------------------------------------------------------------------------------- |
| Projects    | Ordered Project resources, absolute roots, default-working-root order, and current Thread usage summary           |
| Models      | Route, explicit authentication kind, safe settings, and compatible account relationship without credential values |
| Agents      | Model, instructions, Capability selections, tools, Plugin/MCP defaults, and ordered subagent roster               |
| Subagents   | Canonical Markdown frontmatter and instruction body plus explicit external import                                 |
| MCP         | Command or remote transport, credential environment references, selection usage, and validation                   |
| Extensions  | Harness Plugins, Environment profiles, and Environment Run Extensions as distinct resource kinds                  |
| Defaults    | Root process logging settings and new-root-Thread defaults; Web listener access remains outside this tree         |
| Catalog     | Installed Capability and extension availability, provenance, ambiguity, and configurability                       |
| Accounts    | Compatible Codex or Grok status and only the explicit login/logout actions the App advertises                     |
| Diagnostics | Accepted generation, candidate errors, source locations, exact editor navigation, and bounded repair guidance     |

Availability, configured identity, default selection, and current Thread selection are shown as separate facts. Installing a package does not create a resource or enable it. Editing a default does not claim to update an existing Thread.

## Resource Collections

Each collection route provides:

- stable resource ID, display name, kind, and accepted source digest;
- accepted-generation status and candidate diagnostics relevant to that source;
- concise kind-specific summary and known accepted-resource references;
- deterministic search and ordering;
- explicit create, duplicate-as-new, edit, and delete actions permitted by the App.

A filename remains presentation rather than identity. The UI never implies that changing a filename or display name changes the resource ID. Changing an ID is represented as creation of a different resource plus optional deletion of the old source, with validation at each publication; it is not an in-place rename illusion.

The browser can show dependencies derivable from the complete accepted configuration, such as Agents selecting a Model or defaults selecting a Project. It labels this as known configuration usage and never claims it covers retained Thread selections, external files, active Runs, or arbitrary extension behavior unless the App supplies those facts.

## Source Snapshot and Draft

The browser consumes the bounded detached `ConfigurationSourceView` owned by [Detached Surface Contracts](../05-runtime-subagents-and-surfaces.md#detached-surface-contracts). This is an App projection, not a filesystem handle. Its `relative_path` is one App-approved configuration-tree identity and cannot be replaced with an arbitrary absolute path. The browser receives no read-directory or write-path API.

Opening an editor creates a draft with the exact content and digest from that snapshot. The draft tracks local parse status, changed ranges, guided-field validity, and whether a newer summary invalidation exists. It does not update its base digest automatically while dirty.

For a valid YAML resource, guided controls modify a concrete source document while preserving fields they do not own and avoiding whole-document normalization when a safe local edit is possible. Comments and ordering outside an edited value are retained. If malformed source, aliases, unsupported syntax, or a transformation cannot be represented without silent loss, guided controls become read-only and the source editor remains available. Switching editor modes never discards bytes or silently reparses invalid text into a different document.

Canonical Markdown subagents use guided frontmatter controls and one CodeMirror body editor over the same Markdown draft. Arbitrary extension and Capability configuration values use bounded JSON/YAML structured editors; the browser never loads extension-authored JavaScript components or invents a form schema absent from the App catalog.

## Validation

Validation has three distinct levels:

1. **Local syntax** provides immediate YAML, Markdown-frontmatter, JSON, required-field, and simple field-shape feedback without claiming App acceptance.
2. **App preview or mutation validation** parses the exact draft in the complete candidate source tree and applies resource graph, catalog, credential-reference, path, and package-owned configuration validation.
3. **Accepted generation** exists only after the source publication and accepted-generation compare-and-select succeeds.

The UI labels these levels explicitly. A green local editor does not imply that referenced resources, Project roots, installed extensions, or the complete candidate tree are valid. Diagnostics identify the owning source and bounded field location when available, preserve safe App error codes, and never include credential material.

The browser does not send a draft on every keystroke for full App validation. Validation is explicit or debounced after a stable local parse and is cancellable. Save always performs authoritative validation regardless of prior preview.

## Create and Update

Creating a resource starts from a kind-owned minimal canonical document and requires a destination inside the corresponding fixed resource directory. The user selects the stable resource ID deliberately. The browser derives or proposes a safe filename but treats an existing destination or ID as a conflict rather than auto-renaming it.

Create submits `expected_source_digest=null`. Update submits the exact base digest. Both submit the complete source draft and wait for the App's complete-tree validation and atomic publication. On success, the returned source snapshot and accepted generation replace the draft base, relevant collections are invalidated, and the editor remains on the stable resource route.

A summary invalidation received while an unmodified draft is open can refresh the source automatically. If the draft is dirty, the UI marks the base stale and leaves local text untouched. Save then either succeeds against the exact base or receives a conflict.

## Delete

Delete is an explicit destructive command naming the exact source digest. The confirmation displays kind, stable ID, source path, and known accepted-configuration references. A referenced source can be blocked by candidate-generation validation. A Project or other resource still named by a retained Thread can be deleted only if the owning App contract permits it; the UI warns that retained Threads can become unresolved and does not invent a cascading reassignment.

A successful deletion removes the route from the accepted collection and navigates to the nearest stable collection state. A conflict or validation failure leaves the source and editor unchanged. Delete never removes compatible account credentials, extension packages, immutable Run history, or retained Threads by implication.

## Conflict and Rebase

A stale update or delete enters an explicit conflict view with:

- the draft base content and digest;
- the user's current draft;
- the latest App-observed source content and digest;
- a structured or textual diff appropriate to the resource kind.

The browser does not auto-merge YAML, Markdown, ordered selections, or identifiers. The user can discard the draft, copy selected changes into the current source, or replace the draft base after an explicit review. A subsequent save uses the latest digest and exact reviewed content. The old request is never replayed with a new precondition.

If the source disappeared, recreation remains a new no-clobber create. If another source now owns the same stable ID, the conflict cannot be resolved by overwriting its file.

## Projects and Defaults

The Project editor manages name, position, and an ordered non-empty root list. Roots are entered as explicit paths and validated by the App; the browser receives no directory enumeration capability. Reordering roots visibly changes the default working directory and mount order for later Runs. The UI explains that existing admitted Runs are unchanged and that Environment state identity can change with profile behavior or root path.

Project is both the WebUI's root-Thread organization and the owner of roots used by execution; Configure does not create a separate Workspace layer. Deleting a Project can leave retained Threads with an unresolved Project ID, and the Threads area continues to expose those Threads through All Projects for explicit reassignment.

The defaults editor distinguishes scalar default selection from exact ordered Plugin, Run Extension, and MCP collections. It states that defaults initialize new root Threads only. Clearing a collection means select none; omission is not represented as a persistent browser-only inheritance state.

Process editing includes accepted root-file process settings such as logging and identifies them as restart-bound. A successful edit can enter the accepted generation immediately while the current process retains its startup logging behavior until the next App lifetime. HTTP host, API key, and `--dangerously-bypass-permission` are displayed only as current listener facts in diagnostics and cannot be written into `a13n-ui.yaml`.

## Models and Accounts

A Model editor always shows one explicit authentication kind. API-key authentication accepts only an environment-variable name and never a literal secret. Subscription authentication links to the compatible provider account status without copying tokens into the Model draft.

The Accounts area displays provider, safe account identity when supplied, expiry/required-action status, effective store policy, and available actions. It exposes login only when the App has a registered provider flow. Account switch, reauthentication, and logout describe their effect on the shared compatible product store and require explicit confirmation where the App contract requires it.

OAuth authorization URLs can open in a new browser context, but the React application receives no authorization code, PKCE verifier, access token, refresh token, complete identity claim, or raw account-store document. Provider callbacks and credential exchange terminate in the Host adapter and provider flow; the WebUI receives only bounded operation state and completion projections. Closing the login window does not imply cancellation or logout unless the App reports that result.

## Agents and Subagents

The Agent editor preserves the distinctions among:

- one Model selection;
- ordered declarative Capability selections and capability-owned configuration;
- `null`, empty, and exact Agent Plugin/MCP defaults;
- `null`, empty, and exact visible-tool allowlists;
- instructions;
- an ordered roster of Agent-resource and Markdown-subagent references.

Immediate roster names and graph diagnostics are shown before save when locally derivable, but complete finite-graph and catalog validation remains App-owned. Reordering a roster is behavior-affecting. The UI never exposes a Python import target or a separate fine-grained child permission graph.

Canonical Markdown editing supports only its accepted frontmatter and body contract. Unsupported external-product permissions, hooks, Skills, MCP, or provider behavior appear as import diagnostics rather than hidden mappings.

## External Subagent Import

Import is a guided preview flow. The user chooses one product and explicit user or Project scope, then reviews every detected candidate, normalized target ID, rendered canonical Markdown, unsupported-field diagnostics, and target conflict. Preview has no write effect.

Apply submits only explicitly selected ready candidates through the App no-clobber operation. It never modifies source-product files, silently renames a conflict, or establishes synchronization. Each created file enters the accepted generation only after complete-tree validation.

## Extensions, Capabilities, and MCP

Catalog and configured resource views remain distinct:

- Catalog shows installed or Host-registered keys, provenance, ambiguity, and configurability.
- Configure shows file-defined instances with stable IDs and settings.
- Agent and Thread views show exact selections.

Harness Plugin, Environment Provider/profile, and Environment Run Extension resources use separate forms and labels even when listed in one Extensions area. Capabilities appear in Agent composition and the catalog rather than as a false fourth Plugin resource kind.

MCP transport forms are discriminated. Command transport separates command, ordered arguments, and environment-variable references. Remote transport accepts credential-free HTTPS or the App's narrow loopback HTTP exception. Redirect and credential-forwarding policy remain Host-enforced and are explained without exposing resolved headers.

## Configuration Diagnostics

Configuration diagnostics distinguish:

- current accepted-generation digest;
- current candidate source validity and safe errors;
- exact owning source and bounded field location;
- catalog ambiguity or unavailable configured keys;
- compatible account required actions relevant to configuration.

Selecting a source diagnostic opens its exact Configure resource and preserves the diagnostic location. Repair remains an ordinary source draft and expected-digest mutation; the diagnostic view cannot patch a normalized field behind the source authority. Reloading configuration or refreshing a catalog uses the corresponding App command.

Authenticated App status, listener access, schema compatibility, and current-App-lifetime Thread and Run inspection remain in Debug. Neither area presents arbitrary logs, SQLite tables, immutable-object paths, native stack traces, API keys, Model credentials, OAuth material, or environment-variable values.

## Failure Semantics

| Failure                                             | Management behavior                                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Local source syntax invalid                         | Keep source editing available, disable unsafe guided transformations, and show local diagnostics |
| Complete candidate validation fails                 | Keep prior accepted generation active and preserve the draft with App diagnostics                |
| Source changes while draft is clean                 | Refresh the source snapshot and base digest                                                      |
| Source changes while draft is dirty                 | Mark stale and require explicit conflict/rebase handling                                         |
| Expected digest conflicts                           | Never overwrite; present base, draft, and current source                                         |
| Publication succeeds but generation selection fails | Show App failure and refetch source plus accepted-generation authority                           |
| Account operation is unavailable                    | Hide or disable the action based on the App projection; do not synthesize a browser flow         |
| Catalog key is missing or ambiguous                 | Preserve the configured source and present it as unavailable; never choose a similar key         |
| Import candidate cannot map safely                  | Keep it unapplied and show unsupported behavior explicitly                                       |

## Invariants

01. Every resource save publishes complete source text through the App's expected-digest boundary.
02. Guided and raw editing are two views of one exact draft.
03. Invalid source always remains recoverable through raw editing.
04. A browser-local validation success never claims complete candidate-generation acceptance.
05. Conflict handling never substitutes a new digest into an old unreviewed draft.
06. Deleting a resource never cascades into Threads, history, packages, or credentials by implication.
07. Availability, configured identity, default selection, and Thread selection remain distinct.
08. Secret values and compatible account documents never enter resource drafts or browser diagnostics.
09. Configuration diagnostics navigate to exact source repair; they do not duplicate App, listener, or Run inspection owned by Debug.
10. Extension packages contribute schemas and validation only through trusted Host contracts, never executable browser components.
11. Direct file editing and WebUI editing converge on the same accepted-generation authority.
