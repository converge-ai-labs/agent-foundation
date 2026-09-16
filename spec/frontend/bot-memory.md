# Bot Memory

## Design Position

Bot Memory presents a provider-independent memory index and immutable memory documents for collaborative conversations, with one visibility setting per group. [Bots Integration](bots.md) owns the Account-backed Bot and platform onboarding. This contract owns the product model, browsing, sharing interactions, and the requirements those interactions place on backend integration. [Service Long-Term Memory](../a13n-service/42-memory.md) owns Providers, current content APIs, authorization dispatch, and operation completion.

The product model does not imply that the current backend supplies every required operation. Authorized index enumeration, logical document reads, metadata, group subjects, visibility authority, and date traversal must be supported and validated before dependent controls are enabled. This document declares no wire schema; Service owns the lightweight directory under its [directory and index contract](../a13n-service/42-memory.md#bot-memory-directory-and-index).

## 1. Product Outcome and Backend Boundary

People can inspect what a Bot remembers for a Slack channel or Feishu group, browse by date and kind, add corrections or delete records, and control which other groups can use that knowledge. Bots remain usable without Memory. Memory is opt-in; groups are isolated by default even when they use the same Agent or belong to the same enterprise.

The three-pane browser presents scope navigation, logical memory documents including a `MEMORY.md` index, and selected content. The same product and Agent-facing operations apply whether storage uses Mem0, a file-based backend, or another Provider. A logical `.md` path identifies authorized content; it does not imply a host filesystem path. A saved document is the content unit and may hold a complete topic note rather than one atomic fact. A daily heading groups documents without automatically concatenating them into a daily file.

The current [Service Memory contract](../a13n-service/42-memory.md) provides managed Memory Providers, authorization, Agent selection, and CRUD/search through the neutral Harness MemoryBackend. Its built-in adapters are Mem0 OSS and Mem0 Platform. Backends own document bodies; Service owns Provider resources and a lightweight Bot document directory without a memory-body mirror.

Document-capable adapters write exact text with inference disabled and preserve Service-generated scope and provenance metadata. Service owns group identities, directory pagination, and group visibility; frontend filtering does not establish authorization. Provider choices use the declared document capability, and Service rejects unsupported Bot memory bindings before Provider dispatch. Ordinary thread/agent/user memory retains its existing semantics.

## 2. Ownership and Scope Identity

| Concept                   | Responsibility                                                                                      |
| ------------------------- | --------------------------------------------------------------------------------------------------- |
| a13n Workspace            | Resource ownership and IAM boundary                                                                 |
| Bot / Application Account | External installation and application identity, following Bots Integration                          |
| Platform space            | Slack Workspace or Feishu enterprise; external context, not another a13n Workspace                  |
| Group scope               | Memory for one exact channel/chat, shared across its ordinary discussions                           |
| Direct-conversation scope | Memory for one exact private conversation, isolated from group sharing                              |
| Memory document           | Immutable saved content and metadata; independently readable and deletable                          |
| `MEMORY.md` index         | Bounded, permission-filtered navigation to memory documents; changes as documents and access change |
| Thread / Run              | Existing conversation and execution evidence; no second transcript store                            |

Memory follows the group rather than its selected Agent. Renaming a group, rotating credentials for the same Account, or changing the target Agent preserves scope identity. Stable Account and provider conversation IDs identify the location; display names and external sender-supplied metadata do not.

A Bot is a view of one Application Account, as defined by Bots Integration. Memory introduces no independent `bot_id`. Logical group identity includes the a13n Workspace, Account, and external conversation ID; accepted Organization and Memory Provider namespace boundaries remain in force. The Service and neutral backend contracts own group subject representation; a group is not encoded as a fabricated Agent or User.

The sharing boundary is one Bot and its external installation. Cross-Bot, cross-platform, cross-tenant, and cross-a13n-Workspace sharing are outside this contract. A Bot page shows only its selected platform/installation branch. Slack and Feishu use the same navigation pattern without introducing a multi-account Bot identity.

Re-creating a target for the same Account/group can reconnect retained memory only after current authorization and explicit re-enablement. Replacing an Account or Memory Provider does not silently inherit or migrate its predecessor's memory.

### Account-Owned Memory Provider Selection

An Account references one explicitly selected Memory Provider when its Bot memory is enabled. The Provider owns the endpoint, implementation type, and encrypted credentials; Account configuration stores only the reference and Bot memory policy. Multiple Accounts may select the same visible Provider. The relationship is not one Provider per Account, and sharing an endpoint or Provider never grants access to another Account's records.

Group settings inherit the Account's Provider and control memory enablement, use, save behavior, and sharing. The initial product offers no per-group Provider override. Provider selection belongs to the Account rather than its current Agent; switching the Agent leaves the group's storage binding unchanged. There is no separate Bot record with duplicate identity, credentials, or enabled state. Whether Account-owned memory configuration is stored in Account fields or a one-to-one configuration resource is owned by the Service persistence contract, not a second product identity.

The logical isolation boundary retains Organization, a13n Workspace, Memory Provider, Account, and stable provider conversation identity. Trusted ingress determines write ownership. Service authorizes and adds scope restrictions before retrieval; a model-supplied group ID or metadata filter cannot choose another group's records. Group/date display labels are not the authorization boundary.

Changing the selected Provider explicitly changes the storage target. The UI previews that existing records remain on the old Provider; it performs no automatic migration, namespace reassignment, deletion, or fallback. Account/group disablement retains records while preventing the affected future operations. Accepted Run bindings follow the existing retained-selection principle: later configuration changes do not silently redirect an in-flight or retried operation to a different Provider. Current eligibility and sharing authority still apply, and child Agents cannot expand the parent Bot's authorized conversation scope.

## 3. Daily Records and Long-Term Knowledge

| Kind      | Content                                                                           | Presentation                                              |
| --------- | --------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Daily     | Relevant outcomes, decisions, changes, and handled problems from a dated activity | Date filter or date grouping in the document list         |
| Long-term | Stable background, procedures, preferences, and decisions useful across days      | Topic/title or text excerpt, with optional date filtering |

A daily document may describe a dated release decision; a long-term document may contain a complete dependency-management note and its authorized source associations. Multiple documents can belong to one day or topic. In the lifecycle and storage rules below, a saved memory record means one logical document, not necessarily a single sentence or a native backend row. The product does not concatenate all records for a date into a mutable daily document.

A controlled activity date is interpreted in the conversation's configured time zone and is distinct from creation time. Both are fixed when the record is saved. A later correction has its own dates and never moves the earlier record to a different date group. The backend defines activity attribution and time-zone handling; filters identify the date and time zone they use. The UI must not guess missing date semantics or silently re-bucket history after a time-zone change. Legacy records without date/type metadata remain explicitly unclassified rather than receiving fabricated values.

Only content admitted and processed by a13n under appropriate retention authority is eligible. This is not an archive of all platform messages or an automatic import of historical conversations. Reading a Connector document for a task does not by itself authorize retaining it for the whole group. Source links do not grant access to private discussions or external documents.

### Immutable Records and Corrections

A saved Bot memory document records past information and remains immutable. The derived navigation index is a separate projection, not a historical memory record. Its text, optional title, kind, activity date, source associations, trusted ownership, creation time, and correction references are fixed at successful creation. This applies equally to daily and long-term records. No human role, Agent, background job, or shared recipient can modify these values through a13n after saving.

Creation, reading, and deletion are supported document actions. Visibility is configured for the whole group. There is no Edit action or saved-record update permission. Bot-facing routes, tools, backend facades, and any generic Memory API capable of addressing Bot-owned records must reject update/reclassification/ownership changes before dispatching a provider mutation. Hiding a button or omitting an update tool is insufficient. Mem0 inference or consolidation must not overwrite existing Bot records. The general MemoryBackend update capability remains usable for ordinary non-Bot memory under its existing contract.

Changed circumstances and corrections create a new record with its own ID and trusted creation time. A correction can explicitly reference an authorized earlier record in immutable source metadata; this is not an update to the older record or an automatic deletion. For example, retain “Release planned for Friday” and add “The release was moved to Monday.” Retrieval uses the available chronology and explicit correction relationships for current-state questions while retaining historical context. A later timestamp alone does not prove that one statement supersedes another. Referencing a record never grants access to its text, source, or audience; deleted or inaccessible evidence remains unavailable.

Deletion is permitted under the owning scope's delete authority and uses confirmed completion. It is not an edit or an undeclared delete-and-recreate replacement under the same ID. Group visibility may be administered independently of immutable memory content, with their own audit/completion semantics. Removing access or deleting a record does not erase content already delivered in a conversation.

### Memory Index and On-Demand Reading

Each authorized memory scope exposes a logical `MEMORY.md` navigation entry. It contains short document titles, concise descriptions, and stable logical references rather than full bodies. When memory reading is enabled, the host supplies a compact authorized index as actual model-visible context at the start of the Bot invocation. The Agent reads only the documents or bounded content ranges needed for the task. Search remains available when the index does not reveal the required material. The Console and Agent use the same logical identities and authorization rules; Console selection state does not determine Agent recall. Index text and document bodies are untrusted model context, never instructions or restored authority.

```text
Engineering memory
├── MEMORY.md
├── dependency-management.md
├── release-process.md
└── login-incident.md
```

The names illustrate logical references, not physical storage requirements. A Mem0 adapter can map a bounded document to an exact record containing its body and metadata. A file-based adapter can map it to a durable file. A document is not implicitly assembled from all rows in a group/day; splitting or composing content requires an explicit backend mapping that preserves document identity, provenance, immutable content, deletion, and sharing completion. Document size limits and supported range reads belong to that contract; no adapter may silently truncate a body to fit its native limit.

`MEMORY.md` is mutable derived navigation. The host updates or regenerates it after confirmed creation, deletion, changed visibility or scope eligibility. This does not modify the referenced documents or their immutable metadata. Users are not offered ordinary document Edit, Delete, or Share actions on the derived index. Sharing follows the owning group visibility; an index link never grants access to its targets.

Index entry titles, descriptions, paths, and even existence are permission-filtered before rendering or model injection. A group can navigate its own memory and explicitly authorized shared content; no global Workspace index exposes other groups by default. Reading a link reauthorizes its exact target. Unknown, deleted, withdrawn, or inaccessible targets are not returned through a stale index or cache. Logical references never authorize arbitrary host paths, path traversal, or automatic URL fetching, and an old reference cannot silently resolve to a replacement document after deletion.

Indexes have bounded entry/token budgets. Large scopes use bounded subindexes, continuation, and search rather than loading every index or body. A partial index identifies its navigation limits and provides a supported way to find more; it never implies the scope is complete. Keep the compact initial index separate from ordinary automatic top-k body recall: a Bot does not enable both by default and duplicate context. This changes the Bot retrieval design, not ordinary non-Bot Agent memory behavior.

On-demand reading can reduce initial body loading; it does not guarantee fewer total tokens or lower latency than bounded semantic recall. Validation measures initial index size, retrieved body tokens, tool round trips, and answer coverage against the existing retrieval path. Index regeneration must not require a fresh LLM call on every read. A stale or unavailable index is explicit; supported authorized search may be used without inventing entries or access.

## 4. Recall and Authoring Controls

Group controls are independent; Bot defaults and target inheritance must be coordinated with the reception policy:

| Control                | Behavior                                                                           |
| ---------------------- | ---------------------------------------------------------------------------------- |
| Use memory             | Permit authorized recall and active lookup                                         |
| Save on request        | Permit explicit remember requests when the execution principal has write authority |
| Automatic organization | Extract eligible daily/long-term records from completed work; default is off       |
| Read-only preset       | Enable reads and disable both conversational save paths                            |

Disabling reads/writes retains stored content. Console deletion uses management authorization. The Console provides no manual memory creation or correction form; new memories and corrections originate from authorized group conversations. In-chat correction adds a new record with a protected source association; forgetting deletes an authorized record. Current standard model tools support search/list/add, not delete, so a forget action requires its own supported operation. Neither Console nor model tools expose a saved-record edit action.

The host resolves allowed memory from the authenticated Account, conversation audience, execution authority, and current group visibility. Local writes target the current conversation. The model cannot choose raw scope IDs, arbitrary filters, destination groups, or credentials.

A Bot invocation must not automatically union the Agent's ordinary agent/user memories with group context. Root and delegated executions remain within the authorized Bot boundary; choosing another Agent cannot widen it. Integration with MemorySelection, child execution, and recovery belongs in the owning runtime contracts.

## 5. Group Visibility

Each group has one **Who can read this group's memory?** setting:

| Value          | User-facing choice        | Effect                                                                                                                               |
| -------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `group`        | Only this group (default) | Other groups cannot retrieve the source group's memory.                                                                              |
| `installation` | All connected groups      | Every eligible group connected to this Bot in the same external installation can read the source group's existing and future memory. |

The boundary is one Application Account and its Slack Workspace or Feishu tenant, not all Accounts in an a13n Workspace. Both daily and long-term documents are included, without history cutoffs or participant selection. Newly connected eligible groups can read an open group's historical memory too. Public and private group conversations require verified audience metadata and enabled memory; direct and unknown conversations cannot contribute or receive shared memory. A removed target cannot participate.

Visibility is directional. Opening Product's memory lets Engineering and Support read Product's documents; it does not open Engineering or Support's own memory. The receiving group can keep its own visibility private. Shared reads do not grant modification or deletion rights. Local writes always belong to the executing conversation. No per-document Share action, recipient list, publication copy, or continuing policy editor exists.

The setting changes access to original documents without copying bodies or sending chat messages. Changing back to **Only this group** stops subsequent cross-group reads, including old links, index entries, search results, and in-flight body retrieval reauthorization. Content already delivered to model context or chat messages cannot be recalled. Deleting a source document removes it from all receiving views under confirmed deletion rules.

## 6. Visibility Configuration

The selected group's **Group memory settings** dialog owns visibility alongside enablement, recall, conversational save/forget, and time zone. Its default is **Only this group**. The dialog explains that opening includes all historical and future memory and future connected groups, and that it does not grant reciprocal access. Saving applies the selected value with the scope's current version. Conflicting edits require reloading; errors retain the draft. Cancel changes nothing.

Unverified and direct conversations cannot select installation visibility. Current platform and Service authorization remain mandatory at runtime. Turning memory off retains data while removing that group from sharing eligibility. Visibility changes refresh affected directory, index, detail, and search views without loading Provider bodies to recalculate access.

## 7. Metadata and Storage Direction

The integration extends the existing provider-neutral Memory path. Logical document identity, index enumeration, bounded content reads, and search references form the shared product contract. Mem0 and file-based adapters map that contract to their own storage; neither native row layout nor filesystem layout determines the Console or Agent view. These are conceptual metadata needs, not committed JSON names or an accepted schema:

| Information                                                  | Purpose and authority                                                                    |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Trusted Account/conversation association                     | Group routing and isolation; assigned and checked by Service                             |
| Kind and activity date/time zone                             | Daily/long-term browsing, with controlled validation                                     |
| Title, logical path, and concise navigation description      | Stable document address and bounded index entry; no native storage path or ID is exposed |
| Source category and protected Thread/Run/message association | Attribution and authorized evidence lookup                                               |
| Trusted creation evidence                                    | Display and chronology and auditing                                                      |
| Immutable correction source relationship                     | Track corrections without disclosing private provenance                                  |

Organization, Workspace, Provider, conversation ownership, and audience checks remain authorization boundaries. Models cannot mutate these through arbitrary metadata. Group visibility is Service-owned authority, not an untrusted list of group IDs on a record. Retrieval restricts authorized namespaces and qualifying records before content enters results; global search followed by frontend filtering is unacceptable.

Document-capable adapters preserve and verify required metadata through their native APIs. Each adapter requires independent validation: Mem0's general metadata support does not prove that the deployed native OSS HTTP server can create and return every required field, filter records safely, and enforce the required creation/read/deletion semantics. [Mem0 metadata filtering](https://docs.mem0.ai/open-source/features/metadata-filtering) is a capability reference, not proof that this integration exists.

Service persists a lightweight directory containing document references, titles, concise navigation descriptions, group ownership, kind, dates, and protected provenance associations. Bodies remain in the selected Provider. The [Service directory contract](../a13n-service/42-memory.md#bot-memory-directory-and-index) owns consistency and availability. `MEMORY.md` is generated from authorized directory entries; paging and sharing management use the directory without loading all Provider bodies. The directory does not introduce a physical file store or a second memory-body store. A file-based backend must define durable storage, concurrent access, and isolation before enablement; a disposable Sandbox directory alone is insufficient.

Legacy records without metadata are not automatically attached to a group or shared. Missing metadata is not evidence of an empty collection. Backfill/import and Provider replacement require explicit scope and data handling decisions.

## 8. Frontend and Request Behavior

### Shared browser shell

All memory interactions reuse the same Bot detail shell and consistent tabs, filters, and controls. Bot-level memory storage and defaults use one configuration dialog, available from Settings and directly from the unconfigured Memory page through **Set up memory**. An explicit **Enable memory** switch is separate from storage selection. Enabling requires an enabled Provider with verified document-memory support; loading, discovery errors, and no compatible storage cannot be saved as an enabled binding. Administrators with Provider management permission can add storage through the existing Provider editor without losing their Bot configuration draft, and a successful creation selects the new storage. Provider creation alone does not enable Bot memory or verify connectivity. After first saving a storage binding, the Memory page prompts the administrator to choose groups and configure their memory. Groups are never silently enrolled, and sharing stays off by default. Canceling either configuration step makes no Bot memory change. The Memory page has no global sharing button or More menu. The selected group heading shows its current visibility and a directly labeled **Group memory settings** button. Opening settings retains the selected group. **Memory needs attention** appears only when unfinished operations exist; a paginated first-page count is marked as a lower bound. Failed operation-status loading shows an inline retry. The recovery dialog checks uncertain writes without repeating creation and retries unfinished deletions. Unconfigured groups remain configurable from their Channels/group detail page. Feishu uses enterprise/group labels within the same interaction model as Slack. Responsive, accessibility, localization, and non-success states follow the requirements below.

### 8.1 Three-pane browser

Administrator entry: **Integrations -> Bots -> Bot detail -> Memory**. Group detail links to the same view with its exact scope selected.

Select a group, open its `MEMORY.md` index, and follow a logical document link to read content on demand. The index detail has no document Delete, Share, or Edit actions; immutable document details retain the authorized Delete action. There are no Create memory or Add correction controls, including in empty states. Date/kind filters apply to documents and do not hide the scope index.

```text
Memory scope                  Documents                        Selected: MEMORY.md
Slack                         MEMORY.md                        Engineering memory index
  Acme Workspace              dependency-management.md         - Dependency tools -> document
    #engineering              release-process.md               - Release workflow -> document
    #support                  login-incident.md                - Incident outcome -> document
    Direct conversations
                              Search | Kind | Activity date    Index updates automatically
                                                               Document bodies stay immutable
```

The left pane is a scope tree: platform, external installation, readable groups/direct conversations. The selected scope exposes logical memory documents; their names and paths come from the shared document contract, not native directory listings or fabricated names assigned to arbitrary search hits.

The middle pane keeps `MEMORY.md` as the scope entry point, followed by logical document names/titles, kind/date filters, and optional date grouping. It distinguishes **Local memory** and **Shared with this group** without a separate published-content collection. Search targets the current group or all authorized memory. Selection/filter state survives opening a linked document and returning to its index. Opening the index never fetches every linked body.

The right pane renders either the selected index with clickable authorized document links, or a selected document body with available provenance/date metadata, effective visibility, and authorized actions. It exposes logical document paths but no native filesystem paths, provider record IDs, raw namespaces, credentials, fabricated metadata, or unsupported history. Shared records identify ownership and read-only status. Loading, no matches, bounded results, forbidden access, missing metadata, disabled memory, and dependency failure have distinct states. Narrow layouts retain the selection flow without three simultaneous columns. English/Chinese labels, keyboard use, and focus restoration follow Console conventions.

### Receiving shared memory

A receiving reader sees original documents from groups with installation visibility. Detail identifies the owning group and explains that its memory is open to connected groups. Shared documents expose no Delete, Edit, or Share action and no private correction/source links.

### 8.2 Lazy loading and bounded results

| Interaction           | Data behavior                                                                                      |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| Open scope navigation | Reuse authorized Account/target information; no per-group Mem0 calls for counts                    |
| Choose date           | Local date control; no scan to discover every populated date                                       |
| Select group/filter   | Bounded request scoped to that group and filters                                                   |
| Open index            | Read bounded authorized entry metadata; do not load all linked document bodies                     |
| Open document         | Resolve its stable logical reference; fetch only the selected content or bounded range             |
| Search                | Explicit submission or bounded debounce; cancel superseded calls and ignore stale results          |
| Agent memory use      | Load a compact authorized index first, then read selected documents; search supplements navigation |

Grouping documents and rendering index entries are presentation work. Opening a group, date, or index does not invoke an LLM to summarize every body. Navigation descriptions come from supported metadata; creating substantive summaries is a separate authorized memory-write workflow and never happens implicitly during browsing.

Current OSS lists are bounded without native continuation/completeness guarantees. Search top-k is not complete date enumeration. Local paging states loaded counts rather than claiming a total, export, or complete day. Bot group/date browsing uses the Service directory for admitted documents with truthful continuation; native content/filter capabilities still require validation. Missing support is disclosed rather than emulated by unbounded scans.

Short-lived data caching and duplicate-request coalescing are optional, not authorization caches. Keys bind the principal/access context, Workspace, Account, Provider, query/source scope, group visibility state, and filters. Each request follows canonical authorization. Content changes invalidate affected views; permission/sharing changes prevent reuse under revoked access. Clients clear protected views after loss of access. Revocation is not delayed until cache expiry.

No persisted daily counts, background scan of every group, or second full-content search index is initially required. Logical `MEMORY.md` navigation is distinct from a storage catalog. The Service directory supplies bounded traversal of admitted Bot documents under its consistency contract; rendering Markdown alone does not solve native enumeration limits or import legacy records.

## 9. Authority, Completion, and Failures

Reading, creation, deletion, and visibility administration are distinct permissions. Saved-document editing is not an available permission. Host-maintained index regeneration is derived navigation maintenance, not permission to rewrite memory bodies or change their audiences. Safe Account reads or Agent invocation rights do not grant private memory access. The first release restricts all Web memory management, including directory/index browsing, search, detail, deletion, and group visibility settings, to effective a13n Workspace administrators under [IAM](../a13n-service/33-identity-and-access-management.md#bot-memory-management). Non-admins cannot open the management view or obtain its data through direct API calls; delegated access is not offered. Administrators are trusted to manage connected private-group memory without personal platform membership checks. Setup makes this boundary clear. This does not change runtime group isolation or require group participants to be administrators. Console authority and authenticated external audience eligibility are enforced server-side.

A group/topic with narrower visibility cannot read or contribute to a broader scope unless the adapter establishes compatible audience authority. Bot removal, Account disablement, or unknown audience eligibility blocks affected runtime access. Public/private transitions never automatically publish old records. Recall is optional for execution; authorization is mandatory. Unavailable memory is not presented as retrieved evidence or an empty store.

Current explicit writes persist when called, independently of final Run success. A later Run failure does not roll back a completed write. Readback confirmation is required; uncertainty is `memory_write_unconfirmed`, and callers inspect before repeating. The product must not claim idempotent Mem0 writes, blind retries, transactional multi-record sharing, nor historical record-version replay.

Automatic organization is available only through a lifecycle capability that consumes authorized, durably completed work. Scheduling, duplicate events, unknown writes, deletion fencing, and preservation of correction relationships require design before enablement. Automatic extraction and consolidation create new records rather than updating, merging into, or reclassifying existing Bot memories. A post-commit job does not by itself make provider writes exactly-once. The lifecycle capability owns these completion guarantees; the browser does not implement a second job runner.

Memory creation belongs to the conversation workflow; the Web management surface does not maintain creation drafts. Body and immutable metadata must be confirmed together at creation. Concurrent deletion, visibility changes, and correction-source validation require backend/Service checks; immutable content does not make these independent operations atomic. Partial/unknown operations are visible without automatic repetition.

Revocation or withdrawal prevents subsequent authorized retrieval through that grant. Content deletion, provider cleanup, and erasing old transcripts are separate completion boundaries. Content already delivered in a reply, trace, export, or active model context cannot be retroactively withdrawn. Retention and cleanup duration remain owning-contract decisions, not an implied unlimited archive.

## 10. Acceptance Scenarios

01. New and upgraded groups default to private memory. Changing the selected Agent does not move the group's documents.
02. Opening one group's visibility exposes its old and new daily/long-term documents to existing and subsequently connected eligible groups; recipient memory remains private unless independently opened.
03. Cross-Account, cross-Provider, cross-platform, direct, unknown, disabled, and disconnected groups receive no shared content or metadata.
04. Closing visibility removes documents from recipient indexes, lists, and search and rejects stale direct reads, including a change during Provider I/O. Source documents remain intact.
05. A recipient cannot delete another group's document. Source deletion removes it from all authorized views.
06. One settings dialog shows the persisted visibility; cancel, version conflicts, and save failures do not falsely report success. No per-document sharing or global policy controls remain.
07. `MEMORY.md` reaches the Agent as bounded untrusted navigation. Index browsing does not fetch all bodies; opening an authorized document loads its exact immutable content.
08. Viewer/Runner/Builder and ordinary memory permissions cannot bypass administrator-only management. Model tools cannot change visibility or choose another write scope.
09. Unconfirmed writes do not activate or repeat blindly. An attention notice supports reconciliation and deletion recovery without a permanent miscellaneous menu.
10. Legacy publication copies and policies do not grant access or become installation-visible automatically; original documents remain retained.

## 11. Capability and Compatibility Requirements

A control is enabled only when its backend contract supports its complete observable behavior. The implementation validates these requirements against the selected native OSS HTTP or Platform API rather than inferring them from general Mem0 SDK support:

| Capability               | Required integration behavior                                                                                                                                        |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Group memory             | Trusted group subjects, Provider selection independent of target Agent changes, authorized root/child execution and recovery                                         |
| Index and document reads | Provider-independent identities, bounded permission-filtered index entries, stable links, on-demand body/range reads, and regeneration without rewriting history     |
| Metadata browsing        | Validated immutable creation/read fields, protected provenance, clear date/time-zone semantics, and explicit handling of legacy records                              |
| Filtered listing         | Subject-safe group/date/kind filtering and truthful pagination/completeness reporting; no full-store scan fallback                                                   |
| Management writes        | Confirmed deletion and operation recovery, immutable body/metadata, and rejection of every saved-record update path; uncertain outcomes do not trigger blind retries |
| Automatic organization   | Durable completion eligibility, duplicate handling, deletion fencing, and append-only correction handling                                                            |

Ordinary non-Bot Agent thread/agent/user memories keep their accepted semantics, including their existing authorized update operations. Existing records without group metadata receive no implicit group membership or shared audience. Provider replacement and historical imports do not silently migrate content. A new Bot memory binding cannot change ordinary Agent memory behavior or widen child execution authority.

Logical Markdown navigation does not imply arbitrary filesystem operations, document revision history, complete exports, or saved daily summaries. Backend document content remains authoritative, and storage changes require their owning contract rather than a frontend decision.

## Related Contracts

- [Bots Integration](bots.md)
- [Service Long-Term Memory](../a13n-service/42-memory.md)
- [Harness Memory Integration](../a13n-harness/09-context-and-memory.md#memory-integration)
- [Application Accounts](../a13n-service/40-connectivity/01a-application-accounts.md)
- [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md)
- [Identity and Access Management](../a13n-service/33-identity-and-access-management.md)
- [Console](console.md)

## Memory Settings API Boundary

Console reads and updates the [Bot-owned settings resource](../a13n-service/42-memory.md#bot-memory-configuration), using its `expected_version` independently of Account version. Bot summaries supply separate Account and Memory settings projections. Existing-account selection uses the Bot collection endpoint. Generic Application Account forms neither read nor write memory configuration. This changes API ownership without changing the administrator-only management boundary or document browsing behavior.
