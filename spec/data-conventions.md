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
| `version`    | The owner-defined version of a model. For a revisioned resource, it is the positive integer position of one immutable Revision in that resource's lineage.                                                                 | Usually a positive integer; its exact initial value and increment boundary belong to the model.  |
| `revision`   | A complete immutable content record or materialization associated with a stable resource or artifact. A revision can contain normalized content and aggregate exact references to other revisions.                         | A structured content value. A revision ID, reference, or content digest identifies that value.   |
| `snapshot`   | A complete immutable capture or derived projection frozen at a defined boundary. A snapshot can aggregate revisions, state, configuration, and locks, but is not by default a published member of a resource lineage.      | A structured content value. A snapshot reference or digest identifies that value.                |
| `state`      | The values that describe an owner's condition or continuation data within a named scope. State can be mutable or immutable, transient or durable, and complete or partial as defined by its owner.                         | A typed value or payload; the term alone implies no persistence, immutability, or resumability.  |
| `checkpoint` | A complete state value durably selected at a valid continuation boundary from which the owning workflow can resume or recover.                                                                                             | A state value plus its durable selection or commit fact; it need not be an independent resource. |
| `generation` | An owner-scoped incarnation of a replaceable runtime, attempt, accepted configuration set, or similar lifecycle participant. It distinguishes values, observations, or work whose validity is limited to that incarnation. | An integer or opaque value; ordering exists only when the owning contract defines it.            |
| `fence`      | An exclusion boundary or owner-validated proof used to reject a protected action from a stale, superseded, cancelled, or terminal participant.                                                                             | A lifecycle boundary or a validated token, commonly monotonically increasing when persisted.     |

Immutability and aggregation do not choose between `revision` and `snapshot`. Use `revision` when the value belongs to the published content lineage of a stable resource or artifact. Use `snapshot` when the value is a point-in-time capture or a derived projection outside that lineage. If an owning model does not establish that semantic difference, it exposes only one of the two concepts.

`Revision` and `Snapshot` type names denote the complete content values. Names ending in `RevisionRef` or `SnapshotRef` denote typed references; fields ending in `_id` or `_digest` denote their scalar identities. A Foundation resource and its current structured Revision expose the same `version`; the scalar selects that immutable content without replacing the Revision object. Other models can also expose `version` for their own single version axis. Qualify the name only when the same model or boundary exposes multiple independently meaningful versions, such as Thread `version` and `queue_version`, or when the owning compatibility term is itself established, such as `schema_version`.

A checkpoint is stronger than ordinary state or a point-in-time snapshot: its owner has validated completeness and durably selected it for continuation. A generation identifies an incarnation; a fence excludes obsolete or terminal participants. Neither term supplies content-version or compatibility meaning unless its owning contract states that separately.

## Object Identity

Every independently addressable Foundation-owned object has one stable opaque identifier in the form `<kind-prefix>_<random-suffix>`.

- The kind prefix is a short, stable, lowercase abbreviation of the object kind, such as `org` or `ag`.
- The random suffix contains only lowercase ASCII letters and digits and is generated with sufficient unpredictable randomness by the shared platform generator.
- Prefixes are allocated as object kinds are introduced. The set is open, but an allocated prefix is never renamed, reused, or assigned another meaning.
- An identifier is immutable and is not reused after its object is removed.
- An identifier encodes no tenant, region, time, ordering, parentage, storage, or routing information.
- Possession or recognition of an identifier grants no authority.

First-party Python code uses one shared object-ID generator rather than reimplementing prefix validation, alphabet selection, or randomness. Other languages follow the same observable format when they are responsible for creating Foundation-owned objects. Consumers treat IDs as opaque strings and do not derive behavior from their prefix or suffix.

External identifiers retain the format and semantics of their defining owner. Examples include Pydantic tool-call identities, provider Environment identities, JSON-RPC request identities, tracing identifiers, and caller-owned idempotency keys. Their containing schema preserves enough issuer, provider, or scope information to interpret them without presenting them as Foundation-owned IDs.

Compact model-facing references such as `process-1`, `task-2`, or a scoped subagent reference are selectors, not Foundation object IDs. Their owning contract defines their scope, lifetime, resolution, and reauthorization rules. Handles, cursors, tokens, credentials, and content digests likewise retain their owning formats and never become object IDs by naming convention.

## Resource Revisions and Concurrency

A revisioned Foundation resource has one stable object ID, one positive integer `version` beginning at `1`, and one `current_revision_id`. Its immutable Revision carries the same resource ID and version.

- Creation atomically commits the resource head and Revision `1`.
- A material content change appends one complete immutable Revision, increments `version` once, and selects that Revision as current in the same transaction.
- A canonical semantic no-op returns the current Revision and changes neither `version` nor the representation tag.
- Restoring historical content copies it into a new later Revision; moving the current pointer backward is not rollback.
- `(resource_id, version)` is unique, positive, monotonically increasing, and never reused.
- Durable work records an exact Revision ID or a complete owner-defined snapshot. It never resolves an unqualified `latest` during execution or recovery.
- Absence is represented explicitly rather than by version `0`.

Revision content contains every value whose change must affect durable selection, reconstruction, or historical interpretation. The resource head contains stable identity, user-facing metadata, administrative availability, archival or deletion facts, audit actors, timestamps, `version`, and `current_revision_id`; it does not duplicate mutable Revision content. A metadata-only mutation does not create a Revision or increment `version`.

Revision publication uses `expected_version` against the resource head. Metadata-only and intentionally non-revisioned resource mutations use a strong representation `ETag` and `If-Match`. The tag is concurrency evidence rather than an addressable version or revision. Idempotent replay is resolved before either precondition is evaluated.

Foundation models use `version` for their primary version axis and do not expose a parallel `revision_number` or another generic scalar counter for the same fact. A model with multiple independent version axes qualifies the secondary names just enough to distinguish them. Mutable representations that do not need an addressable or domain-significant version use a strong ETag. Database migrations, protocols, artifacts, packages, and external systems retain their owner-defined compatibility or release semantics.

Sequences, ordinals, offsets, generations, and fences retain their distinct meanings and are not renamed to versions merely because they are numeric. A generation change or fence advance does not create a new domain-object version unless the owning contract commits a corresponding material change.

Package releases, protocol major/minor identities, Git or artifact revisions, and upstream or provider codec versions follow their owning compatibility contracts. They do not become integer domain-object versions by crossing a Foundation boundary.

## Public and Internal Naming

Public APIs and SDKs use concise domain language such as `id`, `model`, `agent`, and `provider`. They do not expose internal suffixes merely to restate meaning already established by the resource, operation, or type.

The primary user-visible name of a managed resource is `name`, and a distinct stable technical selector is `key`. `display_name` is reserved for fields whose owning external or compatibility contract establishes that exact term. Qualified names such as `distribution_name`, `model_name`, and `provider_key` retain their owning semantics.

Internal domain, persistence, event, and adapter models use more explicit names when several identities or selection domains would otherwise be ambiguous, for example an Agent ID and AgentRevision ID beside a provider model identity. Typed values such as an AgentRevision reference or model selector carry semantics that a bare string and naming convention cannot.

A type name states the concept rather than its repository, distribution, or module owner. It does not repeat qualifiers such as `Foundation`, `Service`, `Managed`, or `Workspace` when the surrounding namespace and fields already establish that context. A qualifier is retained only when two real concepts coexist at the same boundary, such as Workspace-Secret and invoking-User-Secret credential sources. Likewise, `managed` belongs in a type name only when the same boundary also exposes a distinct unmanaged form.

Suffixes have stable domain meanings. `Revision` is an immutable member of a resource lineage; `Snapshot` is a frozen capture outside that lineage; `Request` is caller-supplied input; `Selection` records a choice; `Lock` freezes an exact dependency; `Ref` identifies without granting authority; `State` describes an owner's condition; `Event` records an occurrence; and `Receipt` is bounded evidence of an accepted or committed operation. A suffix is not added merely to make a local name sound more explicit.

The public boundary validates and normalizes input once. Internal code consumes the resulting typed meaning instead of repeatedly inferring whether a string is an object ID, symbolic selection, external identity, scoped reference, or secret. No universal field-suffix rule overrides clarity at either boundary.

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
04. A revisioned Foundation resource uses one stable ID, one current Revision ID, and positive integer versions beginning at `1`; the head and current Revision expose the same version.
05. A mutation of a versioned model uses `expected_version`; metadata-only and intentionally non-versioned mutations use an owning strong `ETag` and introduce no parallel generic counter.
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
