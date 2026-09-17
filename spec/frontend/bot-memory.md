# Bot Memory

## Design Position

Bot Memory presents authorized memory documents for collaborative conversations, with topic/date navigation, revision-aware knowledge and procedures, event corrections, explicit publication, and continuing group sharing. [Document Memory](../a13n-harness/21-document-memory.md) owns kinds, revisions, extraction/organization, tools, and file-backed storage. [Service Memory](../a13n-service/42-memory.md) owns managed selections, exact storage bindings, directory publication, authorization, APIs, and completion. [Bots Integration](bots.md) owns the Account-backed Bot and platform onboarding.

Controls depend on the selected backend's verified capabilities. A logical Markdown path is not an arbitrary filesystem path, and a file-looking browser does not prove revision, traversal, or durable-storage support. This document owns the product experience rather than duplicating the Service wire schema.

## 1. Product Outcome and Backend Boundary

People can inspect what a Bot remembers for a Slack channel or Feishu group, browse by type, topic, or date, revise knowledge and procedures, append event corrections, forget documents, and control sharing. Memory is opt-in. Groups remain isolated even when they use the same Agent, enterprise, Provider, or physical storage.

An enabled Bot without an explicit Memory Provider uses the accepted invocation's Environment files. The interface labels that store with its Environment and storage availability, and explains that its lifetime follows the sandbox unless a persistent path is configured. Selecting another sandbox does not move or combine existing memory. A stable group identity establishes ownership but cannot make files survive a deleted sandbox. The browser allows explicit selection of previously admitted storage bindings for a group and never merges them into an apparently complete library.

An explicitly selected Provider or persistent Environment/path follows the Service selection contract. Credentials are never visible in documents or Agent configuration. Missing storage reports unavailable; it does not show a working empty memory store or fall back to a Worker-local directory. A shared persistent store can retain memory across task environments when its binding and authorization are verified.

Backend bodies and revisions remain authoritative. Service owns directory metadata and sharing policy without a second full-content store. Native Mem0 records do not automatically supply document history or every file-backend feature; unsupported operations remain visibly unavailable.

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
| Publication               | Separately approved immutable content and recipient groups, pinned to a source revision          |
| Group-sharing policy      | Continuing access to qualifying documents under current authority                                |
| Thread / Run              | Existing conversation/execution evidence; not another memory transcript archive                  |

A Bot remains a view of an Application Account; there is no independent `bot_id`. Group identity includes the a13n Workspace, Account, and external conversation ID. Rename, credential rotation, and Agent changes do not change ownership. They do not implicitly change a storage binding either. Replacing an Account or Provider never inherits its predecessor's content silently.

Sharing stays within one Bot and external installation. Cross-Bot, cross-platform, cross-tenant, and cross-Workspace sharing are outside this contract. Direct conversations remain isolated from group publication and mutual sharing. Unknown audiences cannot join through a UI setting.

### Account-Owned Memory Selection

Account memory settings select the built-in filesystem default or one explicit visible Memory Provider. Filesystem settings can retain the current Environment default, choose a provider-local root, or reference an explicitly authorized Environment under the Service contract. Group settings inherit that selection and control reading, explicit saving, and automatic organization; there is no per-group Provider override. Disabling memory retains the configured selection and stored content.

The model cannot supply Account/group IDs, storage selectors, ownership metadata, or publication audiences to choose another scope. Accepted Runs retain their exact binding across recovery; later configuration changes do not redirect them. Bot-bound child Agents cannot expand the parent's conversation authority or silently select a child's new sandbox as the parent's memory store.

A changed storage selection previews that existing memory remains on the old target. No implicit migration, all-sandbox search, or fallback is performed. Reconnecting a previously removed group also requires fresh audience validation; a remembered storage locator is not an access grant.

## 3. Memory Types, Revisions, and Dates

The [shared document model](../a13n-harness/21-document-memory.md#memory-types-and-time) defines semantic, procedural, and episodic content. The UI exposes these as knowledge, procedures, and events. Daily/long-term is not another classification axis. Date grouping is a view; a daily summary is explicitly derived content with sources, not an automatic concatenation of all that day's memories.

Knowledge and procedure detail can expose **Revise** to an authorized owner when revision support is available. The editor starts from an exact version, retains sources and applicability, previews the change, and submits its precondition. A conflict preserves the draft and requires a fresh comparison; it never silently overwrites newer content. History shows bounded revision metadata and opens a selected immutable version on demand.

When change records are supported, **Changes** lists committed mutations with their time, actor, trigger, action, and before/after versions. Selecting one loads the exact saved body diff and structured metadata changes on demand, with added/removed lines and any attributed reason and authorized source links. A reason is distinct from the computed diff. A document filter narrows the same scope-level change collection; actor, action, trigger, Run, and time filters execute server-side. This is inspection after saving, not an approval queue or a prerequisite for normal writes.

Revision history and change history remain distinct: full versions show content at a point in time, while changes explain an accepted transition, including path-only or metadata-only changes. Empty body diffs do not hide metadata changes. Unsupported history, removed detail, unavailable storage, integrity mismatch, and an empty authorized list have distinct presentations. Shared-content access does not expose the source's private change history.

Events expose **Add correction**, creating another event with a protected reference. Publications have no Revise action. A later timestamp alone does not establish supersession. Document kind and ownership cannot be changed through a revision editor. Current versions and historical versions are clearly distinguished; retrieving an old version does not restore old access permissions.

Date labels state whether they represent occurrence, applicability, creation, or revision save time. Unknown fields remain unknown. Changing a conversation time zone does not silently re-bucket existing events. Legacy records without a supported type/date are shown as unclassified with retained provenance, not assigned a fabricated semantic kind.

Sources identify authorized messages, completed work, documents, or memory revisions. Reading external material for a task does not automatically authorize retaining it for the group. Private source evidence, deleted sources, and unsupported provenance are not exposed through links or copied into publication metadata.

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

## 5. Sharing Selected Records

The publication dialog opens over the browser and preserves its selected scope, storage binding, filters, and document version.

1. Select one local document and exact source version.
2. Review the proposed publication body. Redaction or an excerpt changes only the unsaved publication draft.
3. Select recipient groups from the eligible authorized list. Source evidence and private group metadata are not included implicitly.
4. Confirm content and audience. Publication stores separately approved immutable content and sends no chat message.
5. Recipients open that approved content under **Shared with this group**.

Publication pins the approved source revision and body. A later source revision does not update the publication. Publishing an updated version requires another explicit preview and confirmation; withdrawing an earlier publication is a separate action. Audience management can change recipients or withdraw access, but cannot edit the approved body or its protected provenance.

Read access alone grants neither revision, deletion, nor resharing. Direct-conversation publication is excluded. A recipient's access to the published body does not grant access to any source revision, private conversation, or evidence.

Source deletion previews and withdraws all derived publications across its revisions. There is no keep-copy option. Deletion is complete only after source deletion and every required withdrawal are confirmed. Concurrent publication cannot leave an active copy of a deleted source. Partial or unknown outcomes stay visible rather than claiming a multi-object atomic operation. Already-delivered text and physical cleanup remain separate completion boundaries.

## 6. Continuing Sharing Between Groups

Sharing settings configure a continuing grant to qualifying source documents, with an exact audience preview and review confirmation. New eligible documents and their later current revisions can be read without separate publication. Source ownership retains write/delete authority. This differs from an explicitly published version, whose body remains frozen.

| Setting       | Choices and meaning                                                                                   |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| Mode          | Independent groups with explicit publications, or mutual sharing within a selected range              |
| Participants  | Manually selected eligible groups by default; all currently eligible groups requires an exact preview |
| Content kinds | Semantic and procedural by default; episodic requires explicit selection                              |
| History       | Newly created documents only by default; include older documents explicitly                           |
| Future groups | Explicit auto-enrollment opt-in, off by default                                                       |

The platform space is this Bot's Slack Workspace or Feishu enterprise. Private groups are marked. Direct conversations and unknown audiences are excluded. Selecting all current groups neither includes historical documents nor enables future enrollment. Opening an existing policy retains its saved choices rather than resetting defaults.

Future-only eligibility uses immutable document creation time and the Service policy/participant cutoffs. Revising old knowledge cannot make it newly created. An eligible document exposes its current committed revision; history navigation applies current authorization. Event corrections are separately created documents but do not disclose an inaccessible original. Storage selection and type conversion cannot silently import older content into a grant.

Participants with several stored corpora are still one conversation audience. A grant does not combine them into one store: browsing identifies the source storage binding, and each read resolves and authorizes its exact locator. Unavailable source storage is disclosed rather than represented as an empty shared collection. A policy grants no general Environment shell/file access.

Independent publications and continuing policies form a union of valid grants. Detail explains why content is accessible. Removing one grant does not imply revocation through another; disabling a policy does not withdraw publications. Removing a participant ends access through that policy while preserving local content.

## 7. Metadata and Storage Direction

The Service directory stores stable document/version references, storage locators, bounded titles/descriptions, trusted ownership, kind, supported time metadata, creation evidence, and protected sources/publication relationships. Bodies, retained revisions, and content-bearing change records stay in the backend. PostgreSQL stores only the query metadata for change lists under [Service change queries](../a13n-service/42-memory.md#change-queries-and-audit); listing changes does not fetch every diff. Generic editable frontmatter cannot change Service authority.

Filesystem storage reuses the selected Environment's file operations. Memory exposes no shell tools and does not execute commands to search or write. This does not restrict another shell/file tool already authorized over the same sandbox. UI configuration must describe the actual boundary: a scoped directory is not OS isolation, and a sandbox path is not a durability guarantee.

Pending writes, committed writes awaiting indexing, unavailable storage, externally modified files, and lost corpora are different states. A dirty search index does not mean the saved body was lost. Reindexing repairs derived metadata; it does not authorize an external edit or reconstruct lost plaintext. A rebuilt sandbox is not presented as a restored library merely because its logical Environment ID is unchanged.

Native document adapters preserve exact text and required metadata through their supported APIs. Their general metadata support does not imply revision support, complete enumeration, transactional writes, or safe filtering. Unsupported features are unavailable rather than emulated by unbounded scans or hidden body copies in Service.

## 8. Frontend and Request Behavior

### Shared browser shell

Administrator entry is **Integrations -> Bots -> Bot detail -> Memory**. Group detail opens the same browser with an exact conversation selection. The toolbar exposes memory and sharing settings. Slack and Feishu retain their platform labels within one interaction model.

### 8.1 Three-pane browser

```text
Conversation / storage       Topic or date navigation       Selected document
Slack                       _index.md                      Title / Kind / Version
  Acme                      semantic/                      Applicability / Sources
    #engineering            procedural/                    TOC and selected section
      Current sandbox       episodic/2026/09/              Revise or Add correction
      Earlier store                                        History / Changes / Delete / Share
```

The left pane selects the conversation and, when necessary, its storage binding. It shows availability and sandbox/persistent configuration without exposing native roots or credentials. It does not fetch every group's content or invent counts. The middle pane provides the root/subindexes, type and date filters, topic paths, and submitted search. **Local memory** precedes **Shared with this group**; other groups are lazy authorized navigation targets, not preloaded bodies.

The right pane renders a bounded index, current document, exact historical revision, or immutable publication. It shows supported TOC, applicability, dates, provenance, access reasons, and actions. Knowledge/procedure owners can revise when supported; events can be corrected; recipients remain read-only. The derived index has no ordinary document Edit/Delete/Share action. Moving a topic does not change its document identity or invalidate exact references.

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

All Web Bot memory management, including index metadata, history, changes/diffs, sources, revision, deletion, publication, and policies, requires effective Workspace Admin authority under [IAM](../a13n-service/33-identity-and-access-management.md#bot-memory-management). Administrators can manage connected private-group memory without proving personal platform membership; setup explains that boundary. Runtime audience validation and execution memory authority remain independent.

Group/topic visibility cannot widen through a path, source link, editable metadata, shared sandbox, or Agent selection. Bot removal, disabled Accounts/scopes, and unknown audiences block affected runtime reads/writes. Public/private transitions do not publish old content. Optional recall failure can leave the Agent usable but must be represented as unavailable memory.

Explicit writes commit independently of eventual Run success. Exact backend readback and Service directory publication determine availability. An interrupted operation retains its idempotency identity; a successful filesystem commit followed by indexing or directory failure is reconciled without blindly repeating the mutation. Native backends without idempotent writes do not acquire that guarantee through a UI retry button.

A backend-confirmed write awaiting Service metadata publication is shown as awaiting reconciliation, without a duplicate-save action or a fabricated successful change entry. Admitted writes still awaiting reconciliation prevent the change list from claiming completeness. Failed/denied attempts and deferred candidates are not committed changes. Change detail is separately authorized and never inferred from a security-audit event alone.

Automatic organization consumes only authorized durably completed work, with current write/source authority, duplicate handling, deletion fencing, and explicit unknown outcomes. Disabling organization prevents pending uncommitted publication under stale settings. Historical revisions and event evidence are not rewritten by consolidation.

Deleting a document withdraws derived publications and removes all its versions and content-bearing change details from authorized retrieval. The deletion preview includes revisions and diffs in the physical-cleanup scope; a retained metadata-only deletion entry cannot reopen removed content. Revocation, source deletion, provider physical cleanup, and erasure of old traces/replies are separate facts. Previously delivered context cannot be retroactively withdrawn. Operation and cleanup status must not imply an unlimited archive or instantaneous physical erasure.

## 10. Acceptance Scenarios

01. Same group, same store, another eligible discussion/Worker reuses memory without changing ownership; another sandbox is visibly a different store.
02. Missing storage configuration selects current sandbox files. No Environment or a failed explicit target never selects Worker-local storage.
03. Sandbox recreation, missing persistent mount, and external file modification are explicit states, not apparently complete empty libraries or old revisions.
04. Knowledge/procedure revision preserves document identity and history; a stale version conflicts. Events and publications reject overwrites.
05. Type/date/topic browsing and TOC reveal bounded authorized metadata, including clear unclassified legacy records and unknown dates.
06. Root/subindex projection and exact section reads stay within budgets with continuation; stale locators do not read newer text at old offsets.
07. Selected publication confirms exact source version, approved text, and recipients, with no chat send and no private evidence leakage.
08. Source revisions do not change published copies. Continuing grants expose eligible current revisions without treating an old revised document as new.
09. New sharing policies default to semantic/procedural, future-only documents, selected groups, and no future enrollment; existing choices remain unchanged.
10. Partial grants, overlapping policies, removal, private audiences, and unavailable source stores retain accurate access explanations.
11. Duplicate completion, cancellation, concurrent revision, lost acknowledgements, dirty indexes, and deletion during organization do not duplicate or resurrect memory.
12. Generic routes, forged metadata, cached results, source references, and child Agents cannot bypass conversation, storage, or current authority boundaries.
13. Source deletion withdraws every derived publication across revisions; partial/unknown completion stays visible.
14. Rebuilding derived indexes preserves document identities, versions, and sources; unsupported backend features stay unavailable.
15. Legacy kind conversion and policy conversion do not silently broaden sharing or rewrite historical evidence.
16. Body and metadata diffs match exact accepted versions, load on demand, and preserve explicit bounded continuation; model explanations do not replace them.
17. Backend success followed by SQL failure reconciles to one committed change; stale writers and failed attempts produce no false success entry.
18. Deletion removes diff content from subsequent reads, and publication/sharing grants or security-audit access cannot reveal source change details.

## 11. Capability and Compatibility Requirements

The Console consumes backend capabilities and per-operation access projections. Document navigation, section search, revisions/history, change lists/diffs, organization, publication, and continuing sharing are independently enabled only with their complete supported contract. `supports_changes` controls change inspection independently of revision support. Native record mode retains its own API and completeness semantics; it cannot bypass Bot document rules.

The three-kind model replaces daily/long-term classification. Legacy records remain inspectable under existing authority, with no guessed kinds. Existing kind-based grants are converted only through the reviewed Service compatibility flow. `_index.md` replaces the `MEMORY.md` presentation name; legacy entry links may resolve to the same authorized index but cannot become mutable documents.

## Related Contracts

- [Document Memory](../a13n-harness/21-document-memory.md)
- [Service Memory](../a13n-service/42-memory.md)
- [Bots Integration](bots.md)
- [Application Accounts](../a13n-service/40-connectivity/01a-application-accounts.md)
- [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md)
- [Identity and Access Management](../a13n-service/33-identity-and-access-management.md)
- [Console](console.md)
