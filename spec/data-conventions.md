# Platform Data Conventions

## Design Position

This document defines the identity, versioning, naming, ownership, and compatibility conventions shared by Agent Foundation data models. It applies to Foundation-owned values across process-local libraries, local Hosts, hosted services, public APIs, SDKs, and project-owned protocols.

Subsystem specifications continue to own their concrete objects, schemas, lifecycles, and protocol fields. Upstream libraries, providers, and external protocols retain authority over the identifiers and versions they define.

## Boundaries

| Concern                                                   | Owner                  | Relationship                                          |
| --------------------------------------------------------- | ---------------------- | ----------------------------------------------------- |
| Foundation-owned object identity and version principles   | This document          | Shared default for every subsystem                    |
| Concrete object catalog, prefix allocation, and lifecycle | Owning subsystem       | Applies the shared principles to its domain           |
| Public API and SDK field names                            | Owning API             | Remain concise while preserving the shared semantics  |
| Internal domain and persistence names                     | Owning implementation  | Make ambiguous identities and selections explicit     |
| Upstream, provider, and external identifiers and versions | Their defining owner   | Preserved rather than re-encoded as Foundation values |
| Authentication and authorization                          | Host or product policy | Never inferred from an identifier or reference        |

## Core Terms

The following terms describe different data and lifecycle axes. They are not synonyms merely because a value is immutable, numeric, content-addressed, or assembled from other values.

| Term         | Canonical meaning                                                                                                                                                                                                          | Representation                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `version`    | The owner-defined version of a model. For a revisioned resource, it is the head's representation version, which every head change advances; a Revision's position in the lineage is its `number`.                          | Usually a positive integer; its exact initial value and increment boundary belong to the model.  |
| `revision`   | A complete immutable content record or materialization associated with a stable resource or artifact. A revision can contain normalized content and aggregate exact references to other revisions.                         | A structured content value. A revision ID, reference, or content digest identifies that value.   |
| `snapshot`   | A complete immutable capture or derived projection frozen at a defined boundary. A snapshot can aggregate revisions, state, configuration, and locks, but is not by default a published member of a resource lineage.      | A structured content value. A snapshot reference or digest identifies that value.                |
| `state`      | The values that describe an owner's condition or continuation data within a named scope. State can be mutable or immutable, transient or durable, and complete or partial as defined by its owner.                         | A typed value or payload; the term alone implies no persistence, immutability, or resumability.  |
| `checkpoint` | A complete state value durably selected at a valid continuation boundary from which the owning workflow can resume or recover.                                                                                             | A state value plus its durable selection or commit fact; it need not be an independent resource. |
| `generation` | An owner-scoped incarnation of a replaceable runtime, attempt, accepted configuration set, or similar lifecycle participant. It distinguishes values, observations, or work whose validity is limited to that incarnation. | An integer or opaque value; ordering exists only when the owning contract defines it.            |
| `fence`      | An exclusion boundary or owner-validated proof used to reject a protected action from a stale, superseded, cancelled, or terminal participant.                                                                             | A lifecycle boundary or a validated token, commonly monotonically increasing when persisted.     |

Immutability and aggregation do not choose between `revision` and `snapshot`. Use `revision` when the value belongs to the published content lineage of a stable resource or artifact. Use `snapshot` when the value is a point-in-time capture or a derived projection outside that lineage. If an owning model does not establish that semantic difference, it exposes only one of the two concepts.

`Revision` and `Snapshot` type names denote the complete content values. Names ending in `RevisionRef` or `SnapshotRef` denote typed references; fields ending in `_id` or `_digest` denote their scalar identities. A revisioned resource head exposes `version` and `default_revision_id`, and each Revision exposes its lineage `number` ([Resource Revisions](#resource-revisions-and-concurrency)). Other models can also expose `version` for their own single version axis. Qualify the name only when the same model or boundary exposes multiple independently meaningful versions, or when the owning compatibility term is itself established, such as `schema_version`.

A checkpoint is stronger than ordinary state or a point-in-time snapshot: its owner has validated completeness and durably selected it for continuation. A generation identifies an incarnation; a fence excludes obsolete or terminal participants. Neither term supplies content-version or compatibility meaning unless its owning contract states that separately.

## Object Identity

Every independently addressable Foundation-owned object has one stable opaque identifier. Allocated identifiers use the canonical form `<kind-prefix>_<random-suffix>`.

- The kind prefix is a short, stable, lowercase abbreviation of the object kind, such as `org` or `ag`.
- Random suffixes use lowercase hexadecimal (`0-9a-f`) from a cryptographically secure random generator. Length follows the owning subsystem's cumulative allocation volume and unpredictability requirements.
- Prefixes are allocated as object kinds are introduced. The set is open, but an allocated prefix is never renamed, reused, or assigned another meaning.
- An identifier is immutable and is not reused after its object is removed.
- An identifier encodes no organization, region, time, ordering, parentage, storage, or routing information.
- Possession or recognition of an identifier grants no authority.

Service Python code uses one shared object-ID generator for prefix validation, alphabet selection, and randomness. Embeddable libraries and other languages follow the same observable format independently of the Service allocator. Consumers treat IDs as opaque strings.

Thread IDs use `thread_` plus 32 lowercase hexadecimal characters. The Service allocates 128 random bits; the embeddable Harness and Harness UI use the hexadecimal form of a UUID4. The Harness and Harness UI also accept `thread-` plus 32 lowercase hexadecimal characters wherever they accept a Thread ID, including persisted state, client-supplied identity and browser drafts, and never rewrite Thread IDs, references or `threads/<thread-id>/` directories. The Service accepts only its own object ID form.

### Service ID Allocation

Service allocates four suffix lengths. The table specifies capacity assumptions, not observed traffic or hard allocation quotas. Each volume is the lifetime cumulative allocation count for one prefix in a shared identity namespace, across all organizations and workspaces, including deleted objects. Independently generated namespaces that can later be combined share that budget. For independent uniform suffixes, the probability of at least one collision is approximately `1 - exp(-n * (n - 1) / (2 * 2**bits))`.

| Suffix length     | Random bits | Allocation class                                                                              | Lifetime volume per prefix | Collision probability at that volume |
| ----------------- | ----------- | --------------------------------------------------------------------------------------------- | -------------------------- | ------------------------------------ |
| 20 hex characters | 80          | Managed identities and configuration resources                                                | `10**7`                    | `4.14e-11`                           |
| 24 hex characters | 96          | Sessions, resource revisions, attachments, and bindings                                       | `10**10`                   | `6.31e-10`                           |
| 28 hex characters | 112         | Runs, attempts, inbox entries, and execution records                                          | `10**12`                   | `9.63e-11`                           |
| 32 hex characters | 128         | High-volume events, coordination identities, authentication workflows, and unclassified kinds | `10**15`                   | `1.47e-9`                            |

The shared Service allocator owns these prefix assignments; callers cannot choose a shorter length:

| Suffix length | Allocated prefixes                                                                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 20            | `ap`, `conn`, `cprov`, `envtpl`, `eprov`, `mdl`, `mem`, `memprov`, `mprov`, `org`, `sa`, `sk`, `usr`, `wprov`, `ws`                                                      |
| 24            | `apr`, `ast`, `env`, `inv`, `mfile`, `rb`, `sess`, `skr`                                                                                                                 |
| 28            | `inb`, `rat`, `run`                                                                                                                                                      |
| 32            | `ase`, `audit`, `connop`, `ctl`, `ect`, `envoper`, `key`, `obx`, `prt`, `req`, `sec`, `sub`, `thread`, `wrk`, `envrenew`; every other valid kind defaults to this length |

Kinds used for claims, worker incarnations, publication generations, or authentication workflows retain at least 128 random bits regardless of their expected volume. New kinds start at 32 characters until their owner assigns a smaller tier against an explicit lifetime volume budget. A deployment expected to exceed a tier's budget must review allocation length before that growth; cleanup does not reset the budget. These probabilities apply per prefix, not to the aggregate probability across all kinds.

Service `ObjectId` accepts exactly the shapes the allocator produces: a kind prefix, an underscore and 20 to 32 lowercase hexadecimal characters ([Service object IDs](a13n-service/02-layout.md#object-ids)). Acceptance checks that shape, not the tier of a particular kind. Unique constraints are the final collision guard; a collision never overwrites or reuses an existing object, and allocation defines no automatic persistence retry.

Authentication secrets, API key secret material, cookie tokens, OAuth state and verifiers, and provider-owned IDs retain their owning generation rules. A public credential record ID is distinct from its secret. The allocation tiers do not shorten those secrets or re-encode external identifiers.

### Other Identity Forms

External identifiers retain the format and semantics of their defining owner. Examples include Pydantic tool-call identities, provider Environment identities, JSON-RPC request identities, tracing identifiers, and caller-owned idempotency keys. Their containing schema preserves enough issuer, provider, or scope information to interpret them without presenting them as Foundation-owned IDs.

Compact model-facing references such as `process-1`, `task-2`, or a scoped subagent reference are selectors, not Foundation object IDs. Their owning contract defines their scope, lifetime, resolution, and reauthorization rules. Handles, cursors, tokens, credentials, and content digests likewise retain their owning formats and never become object IDs by naming convention.

## Resource Revisions and Concurrency

A revisioned Foundation resource has one stable object ID, a mutable head and immutable Revisions. The head carries `version`, its representation version, and `default_revision_id`. Each Revision carries the resource ID and a positive `number` beginning at `1`. The default is the Revision that an unpinned selection resolves; it is not necessarily the highest number.

- Creation atomically commits the head and Revision `1`, which becomes the default.
- Publishing appends one complete immutable Revision numbered one above the highest, conditioned on the head's strong `ETag`. The Revision becomes the default unless the publication asks otherwise.
- A publication whose canonical content equals the current default Revision is a no-op: it returns that Revision and changes neither the head nor its `ETag`.
- Selecting a retained Revision as the default repoints the head under its `ETag`; it appends no Revision.
- `(resource_id, number)` is unique, positive, monotonically increasing, and never reused.
- Durable work records an exact Revision ID or an owner-defined snapshot (never unqualified `latest` during execution or recovery).
- Absence is represented explicitly (`null`/omitted), never by number `0`.

Revision content holds every value affecting durable selection, reconstruction, or historical interpretation. The resource head holds stable identity, metadata, availability, archival/deletion facts, audit actors, timestamps, `version`, and `default_revision_id`. Metadata-only updates leave Revisions unchanged.

Every head change advances `version`, and every conditional mutation (including publication) sends the strong `ETag` in `If-Match`. Idempotent replay resolves before precondition evaluation.

A revisioned model has two version axes: head `version` and Revision `number`. Other models use `version` for their single version axis, qualifying secondary axes only when distinct axes coexist. Mutable representations without addressable history use a strong `ETag`. Sequences, ordinals, offsets, generations, fences, migrations, protocols, and package releases retain their own owner-defined semantics.

## Public and Internal Naming

Public APIs and SDKs use concise domain names (`id`, `model`, `agent`, `provider`). The primary user-visible name of a managed resource is `name`; a distinct technical selector is `key`; `display_name` is used only where an external or compatibility contract requires it. Qualified names (`distribution_name`, `model_name`, `provider_key`) keep their owning semantics.

Internal models use explicit names and typed references when multiple identities coexist (for example, `AgentId`, `AgentRevisionId`, and provider model identity). Type names state the concept without repeating repository, distribution, or module ownership (`Foundation`, `Service`, `Managed`, `Workspace`) unless two distinct concepts coexist at the same boundary (such as `Scope` beside `WorkspaceScope`).

Where configured Providers are resources, each has its own `id` while `type` selects one trusted implementation family (multiple Providers may share a `type` with independent config, credentials, and lifecycle). Each Provider `type` owns the typed model for its non-secret `configuration` and authoritative validation; credentials are described separately and never enter configuration or read projections. Each domain (Model, Connector, Environment, Memory) defines its own Provider contract.

Domain suffixes carry stable meanings: `Revision` (immutable lineage member), `Snapshot` (frozen capture outside a lineage), `Request` (caller input), `Selection` (choice), `Lock` (frozen dependency), `Ref` (non-authoritative identifier), `State` (owner condition), `Event` (occurrence), and `Receipt` (bounded evidence of acceptance/commit). The public boundary validates and normalizes input once so internal code consumes typed values directly.

### Resource Keys

Only Service models have keys: a model is identified by an immutable workspace-unique key and exposes no ID ([resources](a13n-service/04-resources.md#keys)). Every other Service resource, skills, organizations and workspaces included, is identified by its ID alone; its `name` is display text.

## Ownership and Authority

Every identifier, version, and reference has an explicit owner and scope. Foundation-owned IDs, external IDs, scoped references, and secrets are not interchangeable even when their serialized representation is a string.

An identifier or reference supplies correlation only. Every operation still applies current authentication, authorization, lifecycle, kind, ownership, and scope checks. A secret, credential, lease fence, or bearer token is represented through its owning security contract and is never disguised as an ordinary identifier or restored from non-authoritative data.

## Data Evolution

Durable or cross-process data is interpreted under the exact compatibility facts recorded by its owning schema. A persisted value is not reinterpreted through current defaults, the newest object version, or a replacement external integration.

Schemas with an independent compatibility lifecycle carry an explicit schema compatibility identity. Schema, protocol, codec, and package compatibility versions evolve independently from domain-object versions and retain the representation defined by their owner. An unknown required compatibility version fails explicitly unless the owner defines and applies a valid migration.

Data that affects authority, execution behavior, compatibility, or recovery is represented by typed fields. Generic metadata remains non-authoritative and cannot silently introduce those semantics.

## Invariants

01. Every independently addressable Foundation-owned object has one immutable, non-reused, kind-prefixed opaque ID.
02. Foundation-owned object IDs encode no authority, ordering, ownership, or deployment information.
03. External identities and compact scoped references preserve their owning formats and are never relabeled as Foundation-owned object IDs.
04. A revisioned Foundation resource has one stable ID, a head whose `version` advances with every head change and whose `default_revision_id` selects the Revision an unpinned selection resolves, and immutable Revisions numbered from `1`.
05. A mutation of a versioned state model uses `expected_version`; a revisioned resource head, metadata-only and intentionally non-versioned mutations use an owning strong `ETag`; neither introduces a parallel generic counter.
06. Durable work selects exact object versions, immutable revision identities, or an owner-defined execution snapshot rather than resolving `latest` during execution or recovery.
07. Public interfaces favor concise domain language, while internal models make ambiguous meanings explicit through names and types.
08. Identifier possession never replaces authentication, authorization, scope, or lifecycle validation.
09. Durable data is interpreted only through its recorded compatibility facts; unknown required versions fail unless explicitly migrated.
10. A revision and a snapshot denote complete immutable content values; their references, IDs, or digests identify those values.
11. A revision can aggregate other revisions, while a snapshot remains distinct only when it is a capture or derived projection outside a resource's published revision lineage.
12. State becomes a checkpoint only after its owner validates completeness and durably selects it at a continuation boundary.
13. Generations distinguish replaceable incarnations, and fences reject obsolete or terminal participants; neither is a content version by default.
14. Type names state their concepts without repeating repository, distribution, or module ownership already established by their namespace.
15. Managed resources expose their primary user-visible name as `name`; a distinct stable technical selector is `key`, and `display_name` exists only when an owning external or compatibility contract establishes that term.
