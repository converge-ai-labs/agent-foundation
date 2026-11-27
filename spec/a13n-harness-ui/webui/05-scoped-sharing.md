# Scoped Sharing

## Design Position

A sharing link grants temporary browser access to a Project or a root Thread scope. Guests participate in the same conversation and execution as the instance owner. The App owns grants, scope checks and existing execution; the browser presents a focused guest experience. Instance API keys retain administrative authority.

Prompt guests are trusted collaborators. Their input uses the Thread's existing Agent, tools, Environment and approvals. A sharing scope limits browser navigation and API access; it does not sandbox Agent execution or constrain cross-Thread tools. The share panel explains this authority before creating a prompt link. [Projects and Environments](../04-projects-threads-and-environments.md) retains execution ownership.

## Grant and Lifecycle

Each grant has a generated `share_id`, immutable `scope` (`project` or `thread`) and `target_id`, mutable `permission` (`view` or `prompt`), creation and expiry timestamps, and an optional revocation timestamp. Creation offers one hour, one day or seven days. SQLite retains the grant and a digest of an unpredictable 256-bit bearer key, never its plaintext. Grants survive server restarts until expiry or revocation.

The owner can inspect active grants, change permission without changing the link, and revoke a link. Expiry and revocation are terminal. The key is returned only on creation; losing a creation acknowledgement does not trigger an automatic retry. The owner can inspect and revoke an unusable grant before creating a replacement. Opening the share panel creates no grant.

Links use a fragment credential, removed before mounting network consumers. Guest credentials are retained separately from the instance key and never replace it. Ordinary request URLs, page metadata and logs contain no key. Host/Origin admission remains governed by [listener access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access).

## Scope and Permission

Thread access includes the selected root and its child execution lineage. A Coordinator target also includes its directly owned workers and their child execution lineages. The grant follows the current role and ownership, including workers created later and workers of a target promoted after creation. Directly sharing a worker exposes that worker and its lineage only; its `coordinator_thread_id` is null in guest views, and the Coordinator and sibling workers are out of scope.

Project access includes roots currently assigned to the Project and their child execution lineage, including roots created after the link. Moving a root out of the Project removes Project-granted access. A missing target makes the grant unavailable. Memory-maintenance, Live, and Bot Threads are not share targets.

Guest access reports `target_role` for a Thread target and null for a Project target. The Thread-scoped listing returns the target by default. Filtering by `coordinator_thread_id` pages through a Coordinator's unarchived workers in touch order. Thread grants require the filter to equal the target; Project grants require the filter to be in the Project and constrain the result to that Project. Project listings can use `independent_only` to omit workers; the two filters are mutually exclusive on a worker listing.

| Capability                                                                                        | View | Prompt |
| ------------------------------------------------------------------------------------------------- | ---- | ------ |
| Saved transcript and live output within scope                                                     | Yes  | Yes    |
| Conversation attachments                                                                          | Yes  | Yes    |
| Thread context occupancy, usage totals and read-only effective Agent/Model display                | Yes  | Yes    |
| Same-conversation presence                                                                        | Yes  | Yes    |
| Unsent shared draft and editor presence                                                           | No   | Yes    |
| Upload, coedit, send and steer                                                                    | No   | Yes    |
| Create a conversation in a shared Project using its defaults                                      | No   | Yes    |
| Change Agent, Model, Environment or Project configuration                                         | No   | No     |
| Approvals, external results, cancellation and lifecycle management                                | No   | No     |
| Native Host Files, Git, Terminal, Browser desktop, Live, instance settings and sharing management | No   | No     |

Permissions apply on the server to finite requests and interactive connections. Guests enter an explicitly admitted API surface; existing administrative routes do not accept share keys. Child, operation and attachment identifiers are checked against their owning Thread rather than being treated as capabilities. Project creation ignores no supplied overrides: its narrow schema accepts only a create-only Thread identity and optional title.

Realtime subscriptions authorize the requested root and never expose the instance-wide summary stream or participant directory. A guest sees presence only on authorized conversation pages. Incoming draft edits recheck prompt permission. Expiry, revocation and membership changes end ongoing guest delivery within a bounded connection revalidation interval; they do not cancel already admitted Runs. Permission downgrade disconnects write-capable draft participation and the browser refreshes its permission state.

## Browser Experience

The owner conversation header exposes **Share**, selecting the current conversation or its Project, permission, and lifetime, then presenting a copyable link with visible scope, permission, and expiry. Existing links can be edited (with a separate save action) or revoked without generating new keys. The Include and Send messages options explain when a Coordinator link covers its workers. Language switching follows the [workbench localization contract](04-workbench-interaction.md#localization).

Thread links open at the target conversation. A Coordinator link adds a compact list with the Coordinator pinned above its workers when workers exist or a worker is selected. Project links add a Project-local list, search, and (for prompt guests) New chat. The unfiltered Project list nests workers under expandable Coordinator rows; search includes matching workers directly. Guests can follow transcript links to known in-scope conversations. A missing conversation under a valid grant keeps its conversation-level recovery and navigation.

View mode shows a non-editable read-only composer shell with disabled input controls and no shared draft connection, keeping the Thread's Coordinator or worker identity, Tasks/Notes, context and usage status, and read-only effective Agent/Model summary visible. Prompt mode reuses the shared composer and submission/reconnect rules with the same read-only identity, status, and Model display, noting that the instance owner must answer approval waits. Expired or revoked links show a dedicated sharing-unavailable state distinct from a missing conversation or connectivity loss. Guest auth, query state, and navigation stay isolated from any existing owner session so owners can preview guest links without signing out.
