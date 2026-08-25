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

## Versioning

A versioned Foundation-owned domain object has one stable object ID and a positive integer version beginning at `1`.

- A committed version never changes in place.
- A material change creates a later monotonically increasing version under the same object ID.
- A durable selection records the exact object ID and version. It never relies on an unresolved `latest` selector during execution or recovery.
- Absence is represented explicitly rather than by version `0`.

An owning contract states whether prior versions remain addressable or only the current version is retained. A mutation that protects against a stale write uses `expected_version` against the current version. An idempotent replay is resolved before that precondition is evaluated, and a semantic no-op does not create a new version.

Foundation domain models do not introduce a second generic `revision` counter for mutable objects. `revision` remains valid only when an owning contract uses it for a different established concept, such as a database migration, artifact or protocol revision, routing incarnation, or externally owned term. Sequences, ordinals, offsets, and generations likewise retain their distinct meanings and are not renamed to versions merely because they are numeric.

Package releases, protocol major/minor identities, Git or artifact revisions, and upstream or provider codec versions follow their owning compatibility contracts. They do not become integer domain-object versions by crossing a Foundation boundary.

## Public and Internal Naming

Public APIs and SDKs use concise domain language such as `id`, `model`, `agent`, and `provider`. They do not expose internal suffixes merely to restate meaning already established by the resource, operation, or type.

Internal domain, persistence, event, and adapter models use more explicit names when several identities or selection domains would otherwise be ambiguous, for example an Agent ID and version beside a provider model identity. Typed values such as an Agent version reference or model selector carry semantics that a bare string and naming convention cannot.

The public boundary validates and normalizes input once. Internal code consumes the resulting typed meaning instead of repeatedly inferring whether a string is an object ID, symbolic selection, external identity, scoped reference, or secret. No universal field-suffix rule overrides clarity at either boundary.

## Ownership and Authority

Every identifier, version, and reference has an explicit owner and scope. Foundation-owned IDs, external IDs, scoped references, and secrets are not interchangeable even when their serialized representation is a string.

An identifier or reference supplies correlation only. Every operation still applies current authentication, authorization, lifecycle, kind, ownership, and scope checks. A secret, credential, lease fence, or bearer token is represented through its owning security contract and is never disguised as an ordinary identifier or restored from non-authoritative data.

## Data Evolution

Durable or cross-process data is interpreted under the exact compatibility facts recorded by its owning schema. A persisted value is not reinterpreted through current defaults, the newest object version, or a replacement external integration.

Schemas with an independent compatibility lifecycle carry an explicit schema compatibility identity. Schema, protocol, codec, and package compatibility versions evolve independently from domain-object versions and retain the representation defined by their owner. An unknown required compatibility version fails explicitly unless the owner defines and applies a valid migration.

Data that affects authority, execution behavior, compatibility, or recovery is represented by typed fields. Generic metadata remains non-authoritative and cannot silently introduce those semantics.

## Invariants

1. Every independently addressable Foundation-owned object has one immutable, non-reused, kind-prefixed opaque ID.
2. Foundation-owned object IDs encode no authority, ordering, ownership, or deployment information.
3. External identities and compact scoped references preserve their owning formats and are never relabeled as Foundation-owned object IDs.
4. A versioned Foundation-owned domain object uses a stable ID and positive integer versions beginning at `1`.
5. Stale-write protection uses `expected_version`, not a parallel generic revision counter.
6. Durable work selects exact versions rather than resolving `latest` during execution or recovery.
7. Public interfaces favor concise domain language, while internal models make ambiguous meanings explicit through names and types.
8. Identifier possession never replaces authentication, authorization, scope, or lifecycle validation.
9. Durable data is interpreted only through its recorded compatibility facts; unknown required versions fail unless explicitly migrated.
