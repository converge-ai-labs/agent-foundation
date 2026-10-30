# Scoped Sharing

## Design Position

A sharing link grants temporary browser access to one Project or one root Thread. Guests participate in the same conversation and execution as the instance owner. The App owns grants, scope checks and existing execution; the browser presents a focused guest experience. Instance API keys retain administrative authority.

Prompt guests are trusted collaborators. Their input uses the Thread's existing Agent, tools, Environment and approvals. A sharing scope limits browser navigation and API access; it does not sandbox Agent execution or constrain cross-Thread tools. The share panel explains this authority before creating a prompt link. [Projects and Environments](../04-projects-threads-and-environments.md) retains execution ownership.

## Grant and Lifecycle

Each grant has a generated `share_id`, immutable `scope` (`project` or `thread`) and `target_id`, mutable `permission` (`view` or `prompt`), creation and expiry timestamps, and an optional revocation timestamp. Creation offers one hour, one day or seven days. SQLite retains the grant and a digest of an unpredictable 256-bit bearer key, never its plaintext. Grants survive server restarts until expiry or revocation.

The owner can inspect active grants, change permission without changing the link, and revoke a link. Expiry and revocation are terminal. The key is returned only on creation; losing a creation acknowledgement does not trigger an automatic retry. The owner can inspect and revoke an unusable grant before creating a replacement. Opening the share panel creates no grant.

Links use a fragment credential, removed before mounting network consumers. Guest credentials are retained separately from the instance key and never replace it. Ordinary request URLs, page metadata and logs contain no key. Host/Origin admission remains governed by [listener access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access).

## Scope and Permission

Thread access includes the selected root and its child execution lineage, not independently created sidekick or worker roots. Project access includes roots currently assigned to that Project and their child execution lineage, including roots created after the link. Moving a root out of the Project removes Project-granted access. A missing target is unavailable. Memory-maintenance and Live Threads are not share targets.

| Capability                                                                                        | View | Prompt |
| ------------------------------------------------------------------------------------------------- | ---- | ------ |
| Saved transcript and live output within scope                                                     | Yes  | Yes    |
| Conversation attachments                                                                          | Yes  | Yes    |
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

The owner conversation header places Share beside the icon-only voice control. The share panel first selects the current conversation or its Project, permission and lifetime. Creating a link transitions to a copyable result with visible scope, permission and expiry. Existing links can be managed without generating new keys. Editing permission has a distinct save action; copy never silently changes access.

A single-conversation link opens directly into a focused conversation without global navigation. A Project link provides a compact Project-local conversation list, search and, for prompt guests, New conversation. A guest never sees unavailable management actions as a wall of disabled buttons. Display names remain unverified collaboration attribution under [Collaborative Conversations](01-collaborative-conversations.md).

View mode shows a quiet read-only footer instead of an editor. Prompt mode reuses the shared composer and existing submission, unknown-outcome and reconnect semantics. Approval waits identify that the instance owner must respond. Expired or revoked access presents a dedicated sharing-unavailable state rather than asking for the instance API key. Losing connectivity remains distinguishable from losing access.

Guest pages and owner pages use the same transcript and execution rendering. Guest authentication, query state and navigation stay separate from an existing owner session, so opening one's own sharing link previews the guest experience without logging out of the workbench.
