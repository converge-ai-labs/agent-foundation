# Bot Memory

## Design Position

Bot Memory presents authorized memory documents for collaborative conversations, with topic/date navigation, revision-aware knowledge and procedures, event corrections, and group visibility. [Document Memory](../a13n-harness/21-document-memory.md) owns kinds, revisions, extraction/organization, tools, and file-backed storage. [Service Memory](../a13n-service/42-memory.md) owns managed selections, exact storage bindings, directory publication, authorization, APIs, and completion. [Bots Integration](bots.md) owns the Account-backed Bot and platform onboarding.

Controls depend on the selected backend's verified capabilities. A logical Markdown path is not an arbitrary filesystem path, and a file-looking browser does not prove revision, traversal, or durable-storage support. This document owns the product experience rather than duplicating the Service wire schema.

## 1. Product Outcome and Backend Boundary

People can inspect what a Bot remembers for a Slack channel or Feishu group, browse by type, topic, or date, revise knowledge and procedures, forget documents, and control group visibility. Memory is opt-in. Groups remain isolated even when they use the same Agent, enterprise, Provider, or physical storage.

A legacy enabled Bot without an explicit Memory Provider uses the accepted invocation's Environment files. The interface labels that store with its Environment and storage availability, and explains that its lifetime follows the sandbox unless a persistent path is configured. Selecting another sandbox does not move or combine existing memory. A stable group identity establishes ownership but cannot make files survive a deleted sandbox. The browser allows explicit selection of previously admitted storage bindings for a group and never merges them into an apparently complete library.

An explicitly selected Provider or persistent Environment/path follows the Service selection contract. Credentials are never visible in documents or Agent configuration. Missing storage reports unavailable; it does not show a working empty memory store or fall back to a Worker-local directory. A shared persistent store can retain memory across task environments when its binding and authorization are verified.

Backend bodies and revisions remain authoritative. Service owns directory metadata and group visibility without a second full-content store. Native Mem0 records do not automatically supply document history or every file-backend feature; unsupported operations remain visibly unavailable.

## 2. Ownership and Scope Identity

| Concept                   | Responsibility                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------ |
| a13n Workspace            | Resource ownership and IAM boundary                                                              |
| Bot / Application Account | External installation and application identity                                                   |
| Platform space            | Slack Workspace or Feishu enterprise, not another a13n Workspace                                 |
| Conversation scope        | One exact group or direct conversation, independent of its selected Agent                        |
| Storage binding           | The selected backend/corpus for that scope; includes exact Environment identity for file storage |
| Memory document           | Stable logical identity, kind, and owning scope                                                  |
| Revision                  | Immutable content at one document version                                                        |
| `_index.md`               | Bounded authorized navigation, derived from admitted content                                     |
| Group visibility          | One-way access to current documents from an eligible source group                                |
| Thread / Run              | Existing conversation/execution evidence; not another memory transcript archive                  |

A Bot remains a view of an Application Account; there is no independent `bot_id`. Group identity includes the a13n Workspace, Account, and external conversation ID. Rename, credential rotation, and Agent changes do not change ownership. They do not implicitly change a storage binding either. Replacing an Account or Provider never inherits its predecessor's content silently.

Sharing stays within one Bot and external installation. Cross-Bot, cross-platform, cross-tenant, and cross-Workspace sharing are outside this contract. Direct conversations remain isolated from group sharing. Unknown audiences cannot join through a UI setting.

### Account-Owned Memory Selection

Bot-owned memory settings select one document entry using the explicit backend configuration in [Service Memory](../a13n-service/42-memory.md#agent-selection). Legacy single selections remain readable. The ordinary Agent multi-entry feature does not implicitly add user/Agent memory to Bot conversation context. Filesystem settings can retain the current Environment default, choose a provider-local root, or reference an explicitly authorized Environment under the Service contract. Group settings inherit that selection and control reading, explicit saving, and automatic organization; there is no per-group Provider override. Disabling memory retains the configured selection and stored content.

The model cannot supply Account/group IDs, storage selectors, ownership metadata, or visibility settings to choose another scope. Accepted Runs retain their exact binding across recovery; later configuration changes do not redirect them. Bot-bound child Agents cannot expand the parent's conversation authority or silently select a child's new sandbox as the parent's memory store.

A changed storage selection previews that existing memory remains on the old target. No implicit migration, all-sandbox search, or fallback is performed. Reconnecting a previously removed group also requires fresh audience validation; a remembered storage locator is not an access grant.

## 3. Memory Types, Revisions, and Dates

The [shared document model](../a13n-harness/21-document-memory.md#memory-types-and-time) defines semantic, procedural, and episodic content. The UI exposes these as knowledge, procedures, and events. Daily/long-term is not another classification axis. Date grouping is a view; a daily summary is explicitly derived content with sources, not an automatic concatenation of all that day's memories.

Knowledge and procedure detail can expose **Revise** to an authorized owner when revision support is available. The editor starts from an exact version, retains sources and applicability, previews the change, and submits its precondition. A conflict preserves the draft and requires a fresh comparison; it never silently overwrites newer content. History shows bounded revision metadata and opens a selected immutable version on demand.

When change records are supported, **Changes** lists committed mutations with their time, actor, trigger, action, and before/after versions. Selecting one loads the exact saved body diff and structured metadata changes on demand, with added/removed lines and any attributed reason and authorized source links. A reason is distinct from the computed diff. A document filter narrows the same scope-level change collection; actor, action, trigger, Run, and time filters execute server-side. This is inspection after saving, not an approval queue or a prerequisite for normal writes.

Revision history and change history remain distinct: full versions show content at a point in time, while changes explain an accepted transition, including path-only or metadata-only changes. Empty body diffs do not hide metadata changes. Unsupported history, removed detail, unavailable storage, integrity mismatch, and an empty authorized list have distinct presentations. Shared-content access does not expose the source's private change history.

Event corrections originate from authorized conversations, creating another event with a protected reference. The Console has no Create memory or Add correction form. Events have no Revise action. A later timestamp alone does not establish supersession. Document kind and ownership cannot be changed through a revision editor. Current versions and historical versions are clearly distinguished; retrieving an old version does not restore old access permissions.

Date labels state whether they represent occurrence, applicability, creation, or revision save time. Unknown fields remain unknown. Changing a conversation time zone does not silently re-bucket existing events. Legacy records without a supported type/date are shown as unclassified with retained provenance, not assigned a fabricated semantic kind.

Sources identify authorized messages, completed work, documents, or memory revisions. Reading external material for a task does not automatically authorize retaining it for the group. Private source evidence, deleted sources, and unsupported provenance are not exposed through links or exposed by group visibility.

### Memory Index and On-Demand Reading

The browser and Agent share one logical `_index.md` root and supported subdirectory indexes. Each entry contains a title, concise description, and stable document/directory reference. Directory routes describe topics; document TOC provides section navigation. No parallel `MEMORY.md` body is maintained.

The Host supplies a compact authorized index before task reasoning. Browsing, search, and TOC then lead to bounded exact-version section/range reads. Known references can be opened directly and specific queries can search directly; tree traversal is not mandatory. Index-first mode does not also inject ordinary native top-k bodies by default.

Indexes and search results filter titles, descriptions, paths, and existence before display or model injection. Following a stale link reauthorizes the exact target. Entries fit whole within output budgets and expose continuation; partial navigation never claims completeness. Index regeneration uses admitted metadata without an LLM call or reading all linked bodies. Substantive rewriting is a separate authorized organization operation.

## 4. Recall and Authoring Controls

| Control                | Behavior                                                                                                    |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| Use memory             | Permit authorized index, search, TOC, read, and supported history                                           |
| Save on request        | Permit explicit creation, eligible revision, event correction, and forgetting under current write authority |
| Automatic organization | Extract and organize eligible completed work; default off                                                   |
| Read-only preset       | Enable reads; disable both conversational save paths                                                        |

Console management uses its separate administrator authority. Runtime save and organization do not borrow it. Local writes target the current conversation and retained storage binding. A Bot never unions the Agent's ordinary agent/user memory into group context implicitly, including in delegated execution.

Automatic organization follows the shared extraction workflow and Service durable-completion rules. It can classify candidates, detect duplicates/conflicts, create documents, revise supported knowledge/procedures, and preserve sources. Unresolved candidates do not enter ordinary recall. Organization never installs a Skill or converts recalled procedure text into trusted instructions. The UI distinguishes disabled organization, pending/review-required candidates, confirmed results, and uncertain operations without claiming that a completed Run proves a completed memory write.

## 5. Group Visibility

Each group has one **Who can read this group's memory?** setting:

| Value          | User-facing choice        | Effect                                                                                                                               |
| -------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `group`        | Only this group (default) | Other groups cannot retrieve the source group's memory.                                                                              |
| `installation` | All connected groups      | Every eligible group connected to this Bot in the same external installation can read the source group's existing and future memory. |

The boundary is one Application Account and its Slack Workspace or Feishu tenant, not all Accounts in an a13n Workspace. All document kinds and retained unclassified legacy records are included, without history cutoffs or participant selection. Newly connected eligible groups can read an open group's historical memory too. Public and private group conversations require verified audience metadata and enabled memory; direct and unknown conversations cannot contribute or receive shared memory. A removed target cannot participate.

Visibility is directional. Opening Product's memory lets Engineering and Support read Product's documents; it does not open Engineering or Support's own memory. The receiving group can keep its own visibility private. Shared reads do not grant modification or deletion rights. Local writes always belong to the executing conversation. No per-document Share action, recipient list, publication copy, or continuing policy editor exists.

Visibility exposes current committed revisions, including later revisions of already shared knowledge. It does not expose revision history, change diffs, or private source evidence. Each shared result identifies its source storage binding; unavailable source storage is disclosed and never replaced or unioned with another sandbox. The setting changes access to original documents without copying bodies or sending chat messages. Changing back to **Only this group** stops subsequent cross-group reads, including old links, index entries, search results, and in-flight body retrieval reauthorization. Content already delivered to model context or chat messages cannot be recalled. Deleting a source document removes it from all receiving views under confirmed deletion rules.

## 6. Visibility Configuration

The selected group's **Group memory settings** dialog owns visibility alongside enablement, recall, conversational save/forget, automatic organization, and time zone. Its default is **Only this group**. The dialog explains that opening includes all historical and future memory and future connected groups, and that it does not grant reciprocal access. Saving applies the selected value with the scope's current version. Conflicting edits require reloading; errors retain the draft. Cancel changes nothing.

Unverified and direct conversations cannot select installation visibility. Current platform and Service authorization remain mandatory at runtime. Turning memory off retains data while removing that group from sharing eligibility. Visibility changes refresh affected directory, index, detail, and search views without loading Provider bodies to recalculate access.

## 7. Metadata and Storage Direction

The Service directory stores stable document/version references, storage locators, bounded titles/descriptions, trusted ownership, kind, supported time metadata, creation evidence, and protected source relationships. Bodies, retained revisions, and content-bearing change records stay in the backend. PostgreSQL stores only the query metadata for change lists under [Service change queries](../a13n-service/42-memory.md#change-queries-and-audit); listing changes does not fetch every diff. Generic editable frontmatter cannot change Service authority.

Filesystem storage reuses the selected Environment's file operations. Memory exposes no shell tools and does not execute commands to search or write. This does not restrict another shell/file tool already authorized over the same sandbox. UI configuration must describe the actual boundary: a scoped directory is not OS isolation, and a sandbox path is not a durability guarantee.

Pending writes, committed writes awaiting indexing, unavailable storage, externally modified files, and lost corpora are different states. A dirty search index does not mean the saved body was lost. Reindexing repairs derived metadata; it does not authorize an external edit or reconstruct lost plaintext. A rebuilt sandbox is not presented as a restored library merely because its logical Environment ID is unchanged.

Native document adapters preserve exact text and required metadata through their supported APIs. Their general metadata support does not imply revision support, complete enumeration, transactional writes, or safe filtering. Unsupported features are unavailable rather than emulated by unbounded scans or hidden body copies in Service.

## 8. Frontend and Request Behavior

### Shared browser shell

All memory interactions reuse the same Bot detail shell and consistent tabs, filters, and controls. Bot-level memory storage and defaults use one configuration dialog, available from Settings and directly from the unconfigured Memory page through **Set up memory**. An explicit **Enable memory** switch is separate from storage selection. Enabling requires an explicit filesystem backend or an enabled Provider with verified document-memory support; the saved canonical selection contains one document entry. The filesystem selection describes the current sandbox or explicit Environment/path and its lifetime. Loading or discovery errors cannot be saved as a verified explicit binding; unavailable storage remains explicit at use time. Administrators with Provider management permission can add storage through the existing Provider editor without losing their Bot configuration draft, and a successful creation selects the new storage. Provider creation alone does not enable Bot memory or verify connectivity. After first saving a storage binding, the Memory page prompts the administrator to choose groups and configure their memory. Groups are never silently enrolled, and sharing stays off by default. Canceling either configuration step makes no Bot memory change. The Memory page has no global sharing button or More menu. The selected group heading shows its current visibility and a directly labeled **Group memory settings** button. Opening settings retains the selected group. **Memory needs attention** appears only when unfinished operations exist; a paginated first-page count is marked as a lower bound. Failed operation-status loading shows an inline retry. The recovery dialog checks uncertain writes without repeating creation and retries unfinished deletions. Unconfigured groups remain configurable from their Channels/group detail page. Feishu uses enterprise/group labels within the same interaction model as Slack. Responsive, accessibility, localization, and non-success states follow the requirements below.

### 8.1 Three-pane browser

```text
Conversation / storage       Topic or date navigation       Selected document
Slack                       _index.md                      Title / Kind / Version
  Acme                      semantic/                      Applicability / Sources
    #engineering            procedural/                    TOC and selected section
      Current sandbox       episodic/2026/09/              Revise / History / Changes
      Earlier store                                        Delete / Access details
```

The left pane selects the conversation and, when necessary, its storage binding. It shows availability and sandbox/persistent configuration without exposing native roots or credentials. It does not fetch every group's content or invent counts. The middle pane provides the root/subindexes, type and date filters, topic paths, and submitted search. **Local memory** precedes **Shared with this group**; other groups are lazy authorized navigation targets, not preloaded bodies.

The right pane renders a bounded index, current document or exact historical revision. It shows supported TOC, applicability, dates, provenance, access reasons, and actions. Knowledge/procedure owners can revise when supported; event corrections originate in conversations. Shared recipients remain read-only and receive current content without source history or diffs. The derived index has no ordinary document Edit/Delete/Share action. Moving a topic does not change its document identity or invalidate exact references.

Selection, version, filters, storage binding, and scroll/navigation survive opening a link and returning. Narrow layouts preserve this flow without three simultaneous columns. All controls follow Console keyboard, focus, accessibility, English, and Simplified Chinese conventions.

### 8.2 Lazy loading and bounded results

| Interaction                 | Data behavior                                                                             |
| --------------------------- | ----------------------------------------------------------------------------------------- |
| Browse conversations/stores | Read authorized directory metadata; no provider-wide body scans                           |
| Open root/subindex          | Bounded entries and continuation, without all linked bodies                               |
| Select type/date/topic      | One filtered directory request; no scan to discover all populated dates                   |
| Open TOC/section            | Exact revision-bound locators and bounded content                                         |
| Search                      | Submitted or bounded-debounce query; cancel superseded requests and discard stale results |
| Open history                | Bounded revision metadata; fetch one selected revision separately                         |
| Open changes                | Filtered, paginated committed metadata; fetch one selected diff with bounded continuation |
| Revise                      | Preserve draft and expected version; surface conflicts before another attempt             |

Search ranks candidates and never claims exhaustive date enumeration. Directory continuation is scope/filter-bound. Native bounded lists retain their disclosed incompleteness. Cached responses bind principal, Workspace, Account, Provider/built-in type, store identity, query, document version, and applicable sharing state. Caches do not defer reauthorization or revocation until expiry.

An explicit committed-but-indexing response shows the confirmed document and offers exact read; it does not invite duplicate creation. Unknown writes retain drafts and operation identity for reconciliation. Read failures do not become empty collections. Comparing initial index size alone does not establish total task savings; evaluate evidence coverage, tool round trips, and retrieved tokens.

## 9. Authority, Completion, and Failures

All Web Bot memory management, including index metadata, history, changes/diffs, sources, revision, deletion, and group visibility settings, requires effective Workspace Admin authority under [IAM](../a13n-service/33-identity-and-access-management.md#bot-memory-management). Administrators can manage connected private-group memory without proving personal platform membership; setup explains that boundary. Runtime audience validation and execution memory authority remain independent.

Group/topic visibility cannot widen through a path, source link, editable metadata, shared sandbox, or Agent selection. Bot removal, disabled Accounts/scopes, and unknown audiences block affected runtime reads/writes. Public/private audience changes require current verified eligibility and preserve the configured visibility; they do not independently enable sharing. Optional recall failure can leave the Agent usable but must be represented as unavailable memory.

Explicit writes commit independently of eventual Run success. Exact backend readback and Service directory publication determine availability. An interrupted operation retains its idempotency identity; a successful filesystem commit followed by indexing or directory failure is reconciled without blindly repeating the mutation. Native backends without idempotent writes do not acquire that guarantee through a UI retry button.

A backend-confirmed write awaiting Service metadata publication is shown as awaiting reconciliation, without a duplicate-save action or a fabricated successful change entry. Admitted writes still awaiting reconciliation prevent the change list from claiming completeness. Failed/denied attempts and deferred candidates are not committed changes. Change detail is separately authorized and never inferred from a security-audit event alone.

Automatic organization consumes only authorized durably completed work, with current write/source authority, duplicate handling, deletion fencing, and explicit unknown outcomes. Disabling organization prevents pending uncommitted publication under stale settings. Historical revisions and event evidence are not rewritten by consolidation.

Deleting a document removes it from all receiving views and removes all its versions and content-bearing change details from authorized retrieval. Required legacy-copy cleanup follows Service completion rules. The deletion preview includes revisions and diffs in the physical-cleanup scope; a retained metadata-only deletion entry cannot reopen removed content. Revocation, source deletion, provider physical cleanup, and erasure of old traces/replies are separate facts. Previously delivered context cannot be retroactively withdrawn. Operation and cleanup status must not imply an unlimited archive or instantaneous physical erasure.

## 10. Acceptance Scenarios

01. Same group, same store, another eligible discussion/Worker reuses memory without changing ownership; another sandbox is visibly a different store.
02. Missing storage configuration selects current sandbox files. No Environment or a failed explicit target never selects Worker-local storage.
03. Sandbox recreation, missing persistent mount, and external file modification are explicit states, not apparently complete empty libraries or old revisions.
04. Knowledge/procedure revision preserves document identity and history; a stale version conflicts. Events reject overwrites.
05. Type/date/topic browsing and TOC reveal bounded authorized metadata, including clear unclassified legacy records and unknown dates.
06. Root/subindex projection and exact section reads stay within budgets with continuation; stale locators do not read newer text at old offsets.
07. Duplicate completion, cancellation, concurrent revision, lost acknowledgements, dirty indexes, and deletion during organization do not duplicate or resurrect memory.
08. Generic routes, forged metadata, cached results, source references, and child Agents cannot bypass conversation, storage, or current authority boundaries.
09. Rebuilding derived indexes preserves document identities, versions, and sources; unsupported backend features stay unavailable.
10. Body and metadata diffs match exact accepted versions, load on demand, and preserve explicit bounded continuation; model explanations do not replace them.
11. Backend success followed by SQL failure reconciles to one committed change; stale writers and failed attempts produce no false success entry.
12. Deletion removes diff content from subsequent reads, and group visibility or security-audit access cannot reveal source change details.
13. New and upgraded groups default to private memory. Changing the selected Agent does not move the group's documents.
14. Opening one group's visibility exposes its old and new documents of every kind and retained unclassified legacy content to existing and subsequently connected eligible groups; recipient memory remains private unless independently opened.
15. Cross-Account, cross-Provider, cross-platform, direct, unknown, disabled, and disconnected groups receive no shared content or metadata.
16. Closing visibility removes documents from recipient indexes, lists, and search and rejects stale direct reads, including a change during Provider I/O. Source documents remain intact.
17. A recipient cannot delete another group's document. Source deletion removes it from all authorized views.
18. One settings dialog shows the persisted visibility; cancel, version conflicts, and save failures do not falsely report success. No per-document sharing or global policy controls remain.
19. Viewer/Runner/Builder and ordinary memory permissions cannot bypass administrator-only management. Model tools cannot change visibility or choose another write scope.
20. Unconfirmed writes do not activate or repeat blindly. An attention notice supports reconciliation and deletion recovery without a permanent miscellaneous menu.
21. Legacy publication copies and policies do not grant access or become installation-visible automatically; original documents remain retained.

## 11. Capability and Compatibility Requirements

The Console consumes backend capabilities and per-operation access projections. Document navigation, section search, revisions/history, change lists/diffs, organization, and group visibility are independently enabled only with their complete supported contract. `supports_changes` controls change inspection independently of revision support. Native record mode retains its own API and completeness semantics; it cannot bypass Bot document rules.

The three-kind model replaces daily/long-term classification. Legacy records remain inspectable under existing authority, with no guessed kinds. Legacy sharing policies and publication copies remain inactive; classification never reactivates them or enables installation visibility. `_index.md` replaces the `MEMORY.md` presentation name; legacy entry links may resolve to the same authorized index but cannot become mutable documents.

## Related Contracts

- [Document Memory](../a13n-harness/21-document-memory.md)
- [Service Memory](../a13n-service/42-memory.md)
- [Bots Integration](bots.md)
- [Application Accounts](../a13n-service/40-connectivity/01a-application-accounts.md)
- [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md)
- [Identity and Access Management](../a13n-service/33-identity-and-access-management.md)
- [Console](console.md)

## Memory Settings API Boundary

Console reads and updates the [Bot-owned settings resource](../a13n-service/42-memory.md#bot-memory-configuration), using its `expected_version` independently of Account version. Bot summaries supply separate Account and Memory settings projections. Existing-account selection uses the Bot collection endpoint. Generic Application Account forms neither read nor write memory configuration. This changes API ownership without changing the administrator-only management boundary or document browsing behavior.
