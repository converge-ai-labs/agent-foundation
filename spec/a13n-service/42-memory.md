# Long-Term Memory

## Design Position

Service exposes authorized memory through the built-in filesystem implementation or an explicitly selected managed Memory Provider. Filesystem memory defaults to the accepted Run's Environment, reuses only its file operations, and never falls back to a Worker-local directory. A configured persistent path or explicit Environment can retain the corpus independently of the task sandbox. Mem0 OSS and Platform remain explicit native adapters.

[Document Memory](../a13n-harness/21-document-memory.md) owns document kinds, revisions, change records and diffs, extraction/organization, tools, filesystem layout, and commit semantics. [Harness memory](../a13n-harness/09-context-and-memory.md#memory-integration) owns native record contracts and Capability composition. Service owns Provider resources, trusted subjects, exact storage bindings, authorization, directory publication, change-query metadata, and sharing. Bodies, retained revisions, and content-bearing change details remain in the selected backend; Service keeps navigation and lifecycle metadata without a full-content mirror or search index.

[Agent Management](28-agent-management.md) owns immutable selections and Run overrides; [IAM](33-identity-and-access-management.md) owns grants. Provider and Environment credentials are encrypted, write-only resource fields, never Agent configuration or Harness state.

## Backend and Lifetime

Deployment Provider packages register shared Harness `MemoryBackendPlugin` instances through the `memory` accessor of the existing [Service Provider registration](02-distribution-composition-and-extensions.md#deployment-provider-packages). Built-in types are `a13n.filesystem`, `a13n.mem0-oss`, and `a13n.mem0-platform`. The filesystem type requires no memory credential; its access uses the selected Environment credentials and file authority. Control and Worker use the same selected definitions, configuration schemas, and credential schemas. They do not share Python clients across processes. Registration creates no client and performs no account, database, or network I/O. This is not a Harness behavior plugin and does not make Control import Agent-selected business plugins.

Service assembles a host-owned `MemoryBackendCatalog` from these definitions; embedded hosts may inject their catalog directly. The same backend plugin owns construction in both cases: Service introduces no parallel factory contract or discovery mechanism. Installation alone never selects an external package. Missing implementations fail explicitly without backend fallback.

`memory.timeout_seconds` bounds backend opening, the operation, and write verification, and defaults to 30 seconds. Each dispatch acquires current eligibility and any required encrypted credential snapshot in a short SQL session, closes that session, and obtains the authorized backend outside SQL. Native clients decrypt credentials locally; filesystem operations borrow the retained Environment file binding and its execution guards. The host closes it on completion, failure, or cancellation. The Capability borrows an authorized facade, not an ambient singleton. No live client, endpoint, or credential enters an accepted Run graph or Harness state.

Native backend integration composes public native operations. It requires no upstream source patch, replacement server image, custom route, or direct storage access. The development image pins unmodified upstream server/core sources for repeatable validation; existing deployments providing the same public API can be used directly. The adapter does not add capabilities missing from the upstream server. Deployment changes do not migrate memory data or select a fallback. Switching storage or embedding configuration requires operator-owned data migration and a new Provider resource.

## Filesystem Configuration and Storage Binding

An enabled Agent memory object with no `provider_id` selects `a13n.filesystem` without creating a Memory Provider resource. Its optional `storage` object contains `environment_id` (null/omitted selects the accepted Run Environment) and `root` (default `/memory`, an absolute provider-local path). These are Agent configuration fields, not model tool arguments. An explicit `provider_id` instead owns storage selection; combining it with an Agent `storage` override is invalid. A managed filesystem Provider uses these same storage fields in its immutable configuration and an empty credential schema. A fixed Environment reference belongs to the consuming Workspace; an Organization-owned Provider cannot embed one Workspace's Environment for use in other Workspaces.

These YAML fragments illustrate the serialized Agent memory selection:

```yaml
# Default: current sandbox, provider-local /memory.
memory: {}
```

```yaml
# A persistent volume already mounted within the current sandbox.
memory:
  storage:
    root: /mnt/persistent-memory
  auto_organize: false
```

```yaml
# An explicitly authorized existing Environment; no new target is allocated.
memory:
  storage:
    environment_id: env_example
    root: /memory
```

No Service deployment default names a Worker host path. The Environment provider/deployment owns NFS mounting, credentials, mount identity checks, target retention, and backups. All memory I/O goes through Environment file operations, including on another Worker. Arbitrary local filesystem paths, another user's sandbox, or a newly allocated replacement Environment are never implicit substitutes.

Run acceptance fixes the selected Provider or built-in type, trusted subject, logical Environment selection, root, and behavior flags. Lazy preparation resolves the physical store identity before its first operation and durably associates that identity before admitting content. The storage binding identifies one corpus independently of Worker identity. Current Environment access and memory grants are both required; native memory permission alone cannot acquire arbitrary files. Default selection is allowed without Provider administration, while an explicit Provider requires `memory_provider.read` and an explicit Environment requires its ordinary use authority.

Retries and state-preserving continuations retain the binding. Inline children borrow the same entered file access with their authorized subjects. Async children use fresh adapters; sharing a parent's memory target requires a retained authorized binding, rather than interpreting the child's new execution sandbox as that target. A separately selected child memory configuration obtains its own binding. Bot children retain the Bot binding below and cannot select another corpus or audience.

Directory identities and cursors include the storage binding. The same subject in two sandboxes has separate stores; a new Run using another sandbox does not union or migrate old data. Management can address an old binding explicitly without changing the current Run. A stored Environment ID alone is insufficient after target recreation: verified store identity distinguishes reconnection from data loss. An unavailable explicit path remains unavailable; missing configuration selects the current sandbox, whereas an explicit target failure does not silently create a temporary library. No available Environment means unavailable memory, never local fallback.

A required persistent mount is verified before corpus initialization and writes. A retained corpus that loses its marker/content is not initialized as an empty library. A new corpus after confirmed loss requires explicit initialization and a new storage identity. Initialization, generation revalidation, external edits, and cross-Worker commit guarantees follow [Document Memory](../a13n-harness/21-document-memory.md#storage-binding-and-environment-lifetime).

The file-only memory binding does not remove generic shell/file access elsewhere in the same sandbox. Root-scoped metadata protects memory API authority, not against a principal already allowed to read that sandbox directly. Hosts must not put mutually untrusted subjects' plaintext bodies in a sandbox readable by those subjects. Cross-group private stores require an appropriately isolated explicit Environment or native backend, not just separate folders in a shared task sandbox.

## Memory Provider Resources

A Memory Provider has an immutable `id`, owning Organization and optional Workspace, implementation `type`, validated `configuration`, display `name`, `enabled` flag, write-only object `credential`, safe `credential_configured` status, and creation/update actor and time metadata. Organization-owned Providers are visible in their Workspaces; Workspace-owned Providers are visible only in their owning Workspace. Names are case-insensitively unique within the owning scope.

The configuration and implementation type are immutable in this version. A storage-identity change requires a new resource; rename, disable/re-enable, and credential rotation for the same target are supported in place. Credential rotation is not permission to change the remote storage identity. Disabled resources retain references and remote records. There is no Provider deletion, automatic migration, fallback, or remote cleanup on disable.

Control exposes type definitions at `/api/v1/memory-provider-types` and `/{provider_type}`, and Provider collections at `/api/v1/organizations/{organization}/memory-providers` and `/api/v1/workspaces/{workspace}/memory-providers`. Collection GET/POST and item GET/PATCH follow the standard scope, collection cursor, safe projection, ETag, and audit conventions. PATCH requires `If-Match`; names, enabled status, and credentials are its mutable fields. Item `/references` lists visible Agent Revision references, including retained non-current revisions, with bounded pagination. It is not a claim that all historical Runs have been enumerated.

`memory_provider.read` permits schema/resource/reference discovery and explicit Provider selection when authoring an Agent or changing a Run selection. `memory_provider.manage` permits creation and mutation in the owning scope. Read grants follow the other Provider read actions; builders can manage Providers. These actions do not grant access to memory contents. An invocation of an already configured Agent uses its frozen Provider selection and the existing Agent/subject memory grants, not a new requirement to administer Providers.

## Subjects and Authority

Every memory belongs to exactly one trusted scope. The provider-visible value is `a13n-` followed by SHA-256 of UTF-8 compact JSON `["a13n.memory.v2", organization_id, workspace_id, provider_id, scope, subject_id]`. IDs and scope kinds are immutable inputs. Models cannot select IDs, filters, credentials, or endpoints. For the implicit filesystem selection, the provider identity input is the fixed built-in key `a13n.filesystem`; the directory and corpus locator additionally bind the exact storage identity. Different Provider resources remain isolated even when they point to the same remote endpoint. Deliberate sharing selects the same Provider resource and still respects Organization, Workspace, and subject isolation. Provider identity is part of every namespace, management record locator, cursor binding, and any cache key.

| Scope    | Subject                                         | Mem0 field | Management authorization                                                                            |
| -------- | ----------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------- |
| `thread` | Stable Service Thread ID, preserved across Runs | `run_id`   | Workspace grant, or the Thread's current Run Agent grant; an empty Thread requires Workspace access |
| `agent`  | Stable Agent ID, not Revision or instance ID    | `agent_id` | Workspace or exact Agent grant                                                                      |
| `user`   | Authenticated human User ID                     | `user_id`  | Workspace grant; service accounts cannot select this scope                                          |

Every operation authorizes the Workspace and subject before provider I/O. Agent and Thread ownership must match the selected Workspace and Organization. A supplied memory ID grants no authority: get, update, and delete verify its subject against the authorized namespace. Update and delete pre-read scope before mutation. Provider record subject identity must remain immutable; operators must not reassign record IDs between subjects behind Service.

Each Worker recall or tool call rechecks current Attempt authority, the retained execution principal, root and selected child Agent invocation grants, and current Agent eligibility. IAM grants come from the current Attempt snapshot, not an arbitrary continuous refresh of bindings. Memory read/write actions must cover the selected scope. A direct-Agent-only User does not receive implicit user-wide memory. No SQL session spans provider I/O.

## Agent Selection

`AgentConfig.memory` is absent/null by default. An object enables memory. Omission of `provider_id` selects the built-in filesystem document mode; an explicit Provider selects its declared mode and capabilities.

| Field              | Default                                                | Meaning                                                                          |
| ------------------ | ------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `provider_id`      | null                                                   | Explicit managed Provider, or built-in filesystem when absent/null               |
| `storage`          | current Environment, `/memory`                         | Filesystem selection above; valid only without `provider_id`                     |
| `scope`            | `thread` for filesystem; null for native record recall | Trusted thread/agent/user scope; null native selection searches available scopes |
| `toolset`          | true                                                   | Expose the selected mode's supported authorized memory tools                     |
| `auto_organize`    | false                                                  | Opt into post-commit extraction/organization for a document backend              |
| `recall_required`  | false                                                  | Fail before model work when initial required memory is unavailable               |
| `auto_recall`      | true in native record mode                             | One bounded native recall per logical Harness Run                                |
| `recall_limit`     | 5 in native record mode                                | 1–100 native results                                                             |
| `recall_threshold` | null                                                   | Optional native similarity threshold in [0, 1]                                   |
| `recall_timeout`   | 2 seconds in native record mode                        | Positive native recall timeout, at most 300 seconds                              |

Document mode supplies a bounded authorized `_index.md` projection and the document tools; it does not enable native automatic body recall. Native-only recall fields are rejected when explicitly supplied to filesystem document mode rather than appearing to configure an unused feature. Document reading, explicit writing, and automatic organization each remain subject to current permissions; `auto_organize` does not grant write or source-retention authority.

Each accepted root and child definition retains its own complete selection. Authoring and acceptance validate explicit Provider/Environment visibility; runtime dispatch rechecks eligibility without replacing the accepted binding. A Run override inherits on omission, disables on null, and replaces the complete object otherwise. Backend installation alone enables no memory. An unavailable explicit scope fails instead of falling back. Recall and index results are untrusted context, never instructions or restored authority.

## Document Management API

The Workspace memory-scope collection at `/api/v1/workspaces/{workspace}/memory-scopes` discovers authorized stored bindings. Scope entries identify the subject, Provider or built-in type, safe Environment reference, store availability, and supported operations without native paths or credentials. An exact Environment/subject filter narrows discovery; there is no implicit latest-Run or all-sandbox union. The Bot Account routes retain their existing `/application-accounts/{account_id}/memory-scopes` boundary and administrator authorization. Both surfaces use the same document service; Bot-owned records cannot be reached through ordinary subject grants.

Relative to one selected scope, document operations are:

| Method and suffix                        | Behavior                                                                          |
| ---------------------------------------- | --------------------------------------------------------------------------------- |
| GET `/index`                             | Bounded authorized root/subdirectory `_index.md`, logical path and cursor         |
| GET `/documents`                         | Document metadata with kind/date filters and scope-bound continuation             |
| POST `/documents/search`                 | Query and allowed facets; bounded document/section references                     |
| POST `/documents`                        | Create a three-kind document with explicit sources and a required idempotency key |
| GET `/documents/{document_id}`           | Current or exact requested version, bounded section/range read                    |
| GET `/documents/{document_id}/toc`       | Version-bound heading tree and section locators                                   |
| GET `/documents/{document_id}/revisions` | Bounded supported revision history                                                |
| GET `/changes`                           | Authorized committed-change metadata, with bounded pagination and filters         |
| GET `/changes/{change_id}`               | Exact supported change detail, including bounded diff continuation                |
| PUT `/documents/{document_id}`           | New semantic/procedural revision under `If-Match` and an idempotency key          |
| DELETE `/documents/{document_id}`        | Confirmed deletion and required publication withdrawals                           |

For the Workspace surface, scope paths are `/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}`; Bot scope paths retain their Account prefix. Document references are stable logical IDs, never provider row IDs or filesystem paths. Mutable heads use the shared ETag convention, with exact revision reads returning their version and digest. The [document contract](../a13n-harness/21-document-memory.md) owns body/read budgets, kinds, revisions, and source semantics; native-record limits below do not cap document bodies. Unsupported revision operations are explicit and never overwrite a native record to emulate history. Creation reports committed document identity separately from indexing readiness.

Control management acquires the stored Environment binding under current management and Environment authority outside SQL. It never uses a Control machine's local path as a shortcut. Execution and management coordinate writes to the same corpus through one commit boundary. If a target requires routing to an owning process, the Environment integration must supply it; absence is unavailable, not permission to load a local copy.

## Change Queries and Audit

The backend [change record](../a13n-harness/21-document-memory.md#change-records-and-diffs) owns the diff and detailed explanation. Service stores its query projection in PostgreSQL as `memory_changes`, separate from the document body and revision store. Each row binds a change ID and operation key to Organization/Workspace, subject scope, exact storage identity, document ID, before/after versions, action, Host-confirmed principal and trigger, committed time, optional Run/work correlation, protected backend reference, and record digest. Bot rows also retain the Account/conversation binding. Successful rows are unique by storage identity and change ID and by storage identity, scope, operation key, and document ID. These identities make reconciliation idempotent; they grant no access.

Queryable fields use typed relational columns. The projection contains no diff, old/new field value, free-text explanation, transcript, or copied source evidence. Backend references and integrity digests are internal, not native paths returned to clients. Content-free outcomes and payload-removal evidence can be retained under the Host's audit policy without keeping the erased detail. Domain history is immutable; reconciliation and retention update only their owning publication/availability facts.

`GET /changes` accepts optional exact document, actor, trigger, action, Run, and committed-time filters. Results contain committed metadata only, with stable ordering by committed time and change ID, keyset continuation, and a cursor bound to the current principal, scope, storage, and filters. Authorization precedes pagination. Listing does not open every backend record. `GET /changes/{change_id}` authorizes the exact binding and both represented versions, reads the backend outside SQL, verifies the retained digest, and rechecks current authority before returning a bounded detail page. Continuation binds the record digest. Erased, missing, changed, or unavailable details have explicit states; a stored SQL row never substitutes for a missing diff. Old records without captured changes remain explicitly unsupported or unavailable rather than acquiring invented actors or reasons.

Ordinary non-Bot change access follows current subject memory read authority; Bot change access follows the administrator-only management boundary below. A grant to a published copy or a continuing-sharing participant does not grant the source change history. Source references are independently authorized. The generic security-audit read grant alone does not grant memory-content access.

Service coordinates publication as follows:

1. A short transaction durably admits the operation under its existing idempotency and authority boundary. No success change row is published at admission.
2. The backend commits and verifies the document effect with its required change record outside SQL, using the same operation key on recovery.
3. A short transaction revalidates eligibility and publishes the directory result, change-query metadata, and bounded security-audit success evidence together. Publication checks the expected predecessor and deduplicates the original operation.

PostgreSQL and Environment files do not share a transaction. A verified backend commit followed by SQL failure remains awaiting reconciliation; recovery publishes missing metadata without repeating the content mutation or manufacturing a second change. A committed change is not made visible through Service before its required metadata publication. Pending newer heads do not displace confirmed readable versions. A listing cannot claim completeness while admitted writes still await reconciliation. Cancellation or timeout after possible effects remains unknown until inspected; it is neither a failed write to repeat blindly nor successful audit evidence. Durable recovery is owned by Control under [Control Background Tasks](07-control-background-tasks.md); no database session spans backend I/O.

Document mutations emit [security audit events](33-identity-and-access-management.md#security_audit_events) with action-owned, content-free identifiers and outcomes, including the change ID when known. Failed or denied attempts follow the existing bounded audit path and do not fabricate committed changes. Pending/deferred organization decisions remain operation/workflow evidence. Security audit stores no patch, reason text, old/new value, or raw error. Actor attribution comes from authenticated or retained execution authority, never editable backend fields. Backend changes or digest mismatches cannot be silently imported as trusted Service audit history.

Deletion immediately revokes access to the document's change payloads as well as its revisions. Physical cleanup covers content-bearing backend records and diff caches under the memory retention policy; SQL and security audit can retain permitted non-content evidence. Cleanup progress remains distinct from logical deletion and confirmed publication withdrawal. Restoring a corpus does not restore revoked authority or republish previously erased payloads from stale operation records.

## Native Record Management API

`GET /api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memory-access` accepts the same subject query as content operations. It requires current subject read access and returns `MemoryAccess { can_write: boolean }`, using the same Workspace, direct-Agent, or current-Thread-head authorization as content writes. It performs no backend I/O and grants no durable authority: each content operation authorizes again. This projection does not require Provider administration permission and is not a connectivity check.

All content routes use `/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories`. The Provider is explicit rather than inferred from current Agent configuration, so a caller can address an old Provider after changing the Agent selection. Query parameter `scope` is required. `thread` and `agent` require `subject_id`; `user` forbids it and uses the authenticated User. Resource identifiers are immutable Service IDs, not aliases. The path memory ID remains an opaque native provider identifier.

| Method and suffix     | Input                                                     | Result                      |
| --------------------- | --------------------------------------------------------- | --------------------------- |
| GET collection        | `limit` 1–1,000 (default 1,000), optional native `cursor` | `MemoryCollection`          |
| POST collection       | `{ "text": string }`                                      | 201 `Memory`                |
| POST `/search`        | `{ "query": string, "limit": 20, "threshold": null }`     | `MemoryCollection`          |
| GET `/{memory_id}`    | Subject query                                             | `Memory`                    |
| PUT `/{memory_id}`    | `{ "text": string }`                                      | `Memory`                    |
| DELETE `/{memory_id}` | Subject query                                             | 204 after confirmed absence |

Text is 1–8,000 characters, stored verbatim with inference disabled. Search query is 1–16,000 characters, limit is 1–100, and threshold is optional in [0, 1]. `Memory` contains `id`, `memory`, and nullable finite `score`; no credentials, provider payload, namespace values, or configuration diagnostics are projected. `MemoryCollection` contains `items` and nullable `pagination`. Null `pagination` identifies a bounded result with no traversal or completeness guarantee. A pagination object contains `next_cursor`; null within that object means the final native page. This distinguishes unavailable traversal from exhausted traversal without provider or development-status fields.

OSS listing calls native `GET /memories` once with trusted subject filters and `top_k=limit`, loading at most 1,000 records by default. It returns `pagination=null` and rejects supplied cursors; no private pagination endpoint or fabricated offset exists. Results retain native ordering and expiry filtering. A short or empty list does not prove exhaustion: the native provider can cap or filter its candidate set. The 1,000-row management bound is not a storage or recall limit. Automatic recall searches the provider independently, not this loaded subset.

A client may paginate the already-loaded subset locally, but must describe counts as loaded records rather than a complete total, and must display a bounded-list hint. For example: "Showing up to 1,000 loaded memories. More may exist; search for relevant memories." Local pages are display slices, not server continuation or a complete export. Search is relevance-ranked and is not a substitute for full export either.

Platform preserves native page/page-size traversal, with each request capped at its 200-record page ceiling. The cursor envelope is bound to Provider resource identity, subject, and requested limit; a mismatch is invalid. Clients follow `pagination.next_cursor` only when a pagination object exists. Returned provider URLs are never followed. Search returns bounded results with `pagination=null` on both backends.

## Native Record Completion and Failures

Explicit add requires one completed `ADD` result and a read-back matching subject and exact text. Update verifies the same; delete verifies native not-found after deletion. Provider acceptance, queuing, or an empty response is not durable success. Writes are not idempotent and are never automatically retried.

Authorization denial or a memory outside the selected subject is concealed as not found. Read dependency failure is a bounded `memory_unavailable` error. Failure after possible mutation, including timeout or failed verification, is `memory_write_unconfirmed`; callers inspect current records before deciding to repeat. Cancellation propagates and does not prove rollback. The total backend deadline includes mutation pre-read and verification. Provider error bodies and credentials are not returned.

Native record mode has no automatic transcript extraction, model-visible update/delete, bulk deletion, history API, or provider job polling. Document mode uses the separate versioned tools and opt-in organization contract. Deleting a memory does not erase previously observed text from Thread context, exports, or traces. Retention and erasure of those surfaces belong to their owners.

## Bot Memory Directory and Index

[Bot Memory](../frontend/bot-memory.md) defines the Account-backed conversation product; [Document Memory](../a13n-harness/21-document-memory.md) owns its shared document/revision model. Service durably stores a lightweight directory for those documents. Directory entries contain stable logical document references, Provider locators, bounded titles and navigation descriptions, owning Organization/Workspace/Account/conversation identity, semantic/procedural/episodic kind, supported occurrence/effective times, trusted creation and revision save times, current version and storage binding, and protected provenance and publication associations. These fields support index generation, group/date pagination, and sharing management. They do not form a second body store, vector store, transcript archive, or full-content search index. Provider credentials never enter entries or model-visible references.

The directory covers documents admitted through the Bot memory lifecycle. It does not claim to enumerate arbitrary pre-existing native records. Directory pagination is bound to authorized scope and filters, has stable ordering and explicit continuation, and does not use native search top-k as complete enumeration. Ordinary native-backed management collections retain their existing bounded-list semantics. Browsing a directory page or generating an index reads navigation metadata without fetching every body or calling an LLM. Opening a document resolves its exact Provider locator after current authorization and retrieves its body on demand.

Provider type responses expose the selected factory's `supports_documents`, `supports_revisions`, and `supports_changes` capabilities. The implicit filesystem choice exposes the same descriptors without requiring a Provider resource. Bot Provider selection, group configuration, and document operations require the relevant capability before dispatching Provider work. Change queries require `supports_changes`; lack of that capability does not fabricate history from native CRUD or prevent otherwise supported operations. Unsupported implementations fail explicitly without creating an unconfirmed operation. Ordinary non-document memory remains available on those Providers. Console disables unsupported Provider choices while allowing an existing Bot memory binding to be disabled. A declared capability does not replace operation-time authorization, exact readback, or dependency-error handling.

The Account memory-scope collection accepts an optional exact `target_id` filter. Service validates the live conversation target within that Account and resolves its external conversation identity before filtering scopes by the selected Provider. This bounded SQL lookup does not enumerate every group or contact the Provider. A configured target with no memory scope returns an empty collection; an absent or deleted target, a deleted Account, or insufficient memory management authority is not projected as a working empty store. Cursors bind this filter alongside the Account, Provider and page limit. Group detail navigation uses this exact lookup and keeps document operations in the resulting scope regardless of unrelated scope parameters in the browser URL.

A document or new head becomes available in the directory only after backend commit and exact body, version, subject, and required metadata readback are confirmed. Directory publication checks the expected predecessor and operation key; a verified backend commit followed by SQL failure is reconciled without repeating the backend mutation. A pending newer revision does not replace a still-authorized confirmed head. Unconfirmed writes are not active memories and are not blindly retried. Directory availability, deletion, and publication withdrawal must remain consistent with the owning completion rules: a deleted or revoked document cannot be served from stale navigation, and source deletion completes only with confirmed withdrawal of all derived publications. Missing Provider content or a dependency failure is explicit; directory metadata is never substituted for the missing body. No database transaction spans Provider I/O.

For a Bot invocation with memory reading enabled, Service supplies a bounded, permission-filtered `_index.md` index as actual model-visible context before task reasoning. It includes concise navigation entries for the current conversation and currently authorized shared content. It is not merely a Console view, nor an instruction to the model to guess which memory exists. Explicit document-read operations resolve stable references and reauthorize each read. Index pages keep whole entries within the Harness encoded-context budget, including their continuation cursor. A partial page continues immediately after its last displayed entry without dropping undisplayed entries. Large indexes expose bounded continuation/subindexes or supported search; ordinary automatic top-k body injection is not also enabled by default for the Bot.

The index is derived navigation that changes after confirmed document and access changes. Committed revisions remain immutable; semantic/procedural heads advance only after verified revision publication, while events use append-only corrections. Logical Markdown paths require no physical file mount; Mem0 and file-based implementations expose the same index/document behavior through their supported adapters. Runtime root, child, and Retry access stays within the retained Account/Provider/conversation binding and current authority, independently of Console administrator privileges.

### Retained Conversation Execution Binding

Trusted Slack and Feishu ingress acceptance records `Run.bot_memory` in `bot_memory_json`. The binding contains the Account ID, external conversation ID, explicit Provider or built-in selection, exact storage binding and scope IDs, observed scope version, and read/save/organization flags. The observed store identity is completed through the lazy preparation rule above before content is used. It contains no credentials, body, or native storage namespace. A disabled or unconfigured Bot still retains a disabled binding, so its execution cannot fall back to ordinary Agent/User memory. Other inbound providers retain their ordinary behavior.

Acceptance resolves this binding under the Account and target consistency checks and rechecks the observed scope at commit. Changing the default Agent does not change group identity. Retry, feedback, waiting continuation, asynchronous child Runs, and result successors retain the binding; inline child Agents use the same conversation. A new ordinary inbound invocation can select newer settings. A later Provider selection does not redirect an accepted Run or migrate existing content.

Each runtime operation verifies the current Attempt fence, the persisted Run binding, current root/child Agent invocation authority, and ordinary runtime memory read/write authority. It additionally checks current Account status, configured memory read/save flags, scope enablement, and verified audience eligibility. Current disablement can revoke retained access; later enablement cannot expand an already disabled accepted binding. Console administrator grants are not borrowed by runtime operations. A different child Agent or model-supplied reference cannot change the accepted Account, Provider, or conversation.

Runtime index, TOC, history, read, search, save, revise, and forget operations obtain live platform identity and conversation-membership observations before accessing memory. Checks run outside database sessions and include the current group plus the currently authorized shared source groups needed by a read. Navigation and search admit at most sixteen groups including the current group, with bounded parallel provider calls and a total deadline. Unknown membership, Bot removal, an inactive installation, or a changed/unknown audience prevents the operation from returning memory; failure is not reported as an empty store. Administrators can review a fresh conversation check when configuring the group's name and audience. A historical setup observation alone never authorizes runtime recall.

The live evidence belongs only to that operation and records its credential generation and observed group configuration versions. Directory visibility and post-body-read authorization recheck those versions, the Attempt, current scope settings, and current sharing grants. Credential rotation or a concurrent configuration/grant change cannot reuse the operation's earlier evidence to return newly unauthorized content. Checks neither fetch memory bodies nor expand the retained binding. Previously delivered context cannot be retracted from an already executing model.

Deleting an exact conversation target invalidates its saved conversation check and every retained Provider scope's audience/version in the same transaction. It preserves the directory and bodies for administrator management. Re-creating the target does not restore audience eligibility: a fresh provider check and explicit group configuration are required before runtime memory can reconnect. A check started against an earlier target identity/version cannot repopulate the removed target's observation after deletion.

The worker supplies the authorized document store to Harness document-navigation mode whenever this retained binding allows memory. It suppresses ordinary Agent/User memory for all Bot-bound root and child executions, including disabled bindings. The store uses the canonical directory service for index, TOC, versioned read/search/history, explicit creation/revision, and forgetting. Runtime creation keys combine the Service Run ID and native tool-call ID to protect replay across Attempts. Revision writes require the expected version and current write authority; episodic and publication content cannot be rewritten. Runtime tools expose no publication-management operation.

Administrator document detail identifies the owning group and explains current access through ownership, an approved publication, or matching continuing-sharing policies. Policy explanations reuse the visibility predicates, including kinds, history cutoffs, membership, and enablement, and are recomputed after body-read reauthorization. At most twenty policy explanations are returned; an explicit flag indicates additional grants, whose policies remain browsable through the paginated sharing-management collection. Removing one grant does not conceal another surviving grant. These explanations are management metadata, not additional runtime authority or automatically injected model context. Shared detail does not expose private correction or publication source references.

## Bot Memory Management Authority

[IAM](33-identity-and-access-management.md#bot-memory-management) owns the administrator-only Bot memory management boundary. It applies to document/index browsing, search, TOC, history, change lists/diffs, reads, creation, revision, deletion, publication, and sharing-policy management, including metadata and provenance projections. Safe Account reads, ordinary memory read/write grants, and Agent invocation rights alone do not authorize these management operations. Generic memory routes cannot bypass the Bot boundary, rewrite an immutable revision/event/publication, or replace a revisioned head without its precondition.

Workspace administrators are trusted to manage the connected conversations' memory, including private groups, without proving their personal Slack or Feishu membership. This management grant does not enroll groups in sharing or expand a Bot invocation's runtime audience. Ordinary non-Bot memory permissions and Provider management remain unchanged.

### Continuing Bot sharing policies

A policy belongs to one Account and one explicit Provider or built-in filesystem selection and grants mutual read access among its explicit group participants. The default selects semantic and procedural documents, excludes historical documents, and disables future enrollment. Episodic documents require explicit inclusion. Direct conversations and unknown audiences cannot join. Disabling or removing existing grants remains possible when a participant becomes unavailable; reactivation and newly added participants require eligible group configurations. Independent grants remain a union, so disabling one policy does not withdraw publications or another policy's access.

The API returns the policy's `future_since` and each participant's `joined_at`. Without history, a document's immutable trusted creation `saved_at` must be at or after all three boundaries: policy activation, source participation, and receiving participation. Occurrence/effective dates and revision save times do not determine eligibility. Revising an older document cannot admit it as newly created. An eligible document exposes its current committed revision; access to retained historical revisions still requires current authorization. Explicit publications instead pin an approved source revision and body. Content imported into a genuinely new document is newly retained content and requires independent source-retention and sharing authority; a revision cannot be disguised as a new import to bypass a cutoff. Reactivation and switching from included history to future-only establish a new boundary for all current participants. Removing and explicitly rejoining a group starts its participation again. Other edits preserve existing boundaries.

Future enrollment applies once, when a connected group first saves an enabled memory configuration with a verified public/private audience. Only enabled policies with future enrollment enabled at that moment include it. Previously eligible groups are never backfilled by enabling the option, and reconfiguration, reconnection, or a public/private transition never automatically restores a removed participant. A not-yet-verified group joins only after its first eligible configuration; discovery alone does not enroll it. Existing scopes at schema upgrade are treated as previously configured to prevent rollout from widening access.

Automatic enrollment persists a joined boundary and advances the policy version, so concurrent administrator saves conflict rather than dropping a new participant silently. The 128-group policy bound applies to both explicit and automatic enrollment. When a matching policy is full, first eligible group configuration fails with `sharing_participant_limit` without partial enrollment; the administrator adjusts the policy and retries. Policy writes and first eligible group configuration serialize within the Account, and audit records identify changes without memory bodies.

## Automatic Organization and Compatibility

Service owns durable organization admission after the source work's checkpoint/result has committed. The work key binds the source completion, memory scope, storage identity, and organization policy; duplicate delivery resumes the same work. A short transaction records eligibility and cursor state, model/file work happens outside SQL, and a later transaction confirms outcomes. Current source retention, Bot audience, memory write authority, and target availability are checked again before effects. The workflow does not borrow Console administrator permissions or an expired RunAttempt fence; it uses a Host-owned work lease and retained principal under the post-commit policy.

Only confirmed or explicitly rejected/deferred candidates advance the extraction cursor. Uncertain writes remain reconcilable under their original operation keys. Deletion/revocation fences pending work and forbids stale retries from recreating deleted memory or widening an audience. Turning automatic organization off stops admission and prevents uncommitted work from publishing under an older enabled observation. Completion records identify source references and outcomes without duplicating transcripts. The Harness [organization contract](../a13n-harness/21-document-memory.md#extraction-and-organization) owns candidate semantics and bounded model work.

The three-kind document model replaces daily/long-term classification and all-saved-document immutability. Existing raw native record APIs remain unchanged. Upgrades retain old records and immutable revisions without guessing a new kind, changing creation times, or broadening audiences. Legacy kind-based policies remain frozen to their previously eligible records until explicitly reviewed and converted; they do not silently become a new semantic/procedural grant. Unclassified records remain inspectable under prior authority and are excluded from new type predicates until explicitly classified. An unsupported old client mutation fails validation/preconditions rather than overwriting a current document through the old record contract.
