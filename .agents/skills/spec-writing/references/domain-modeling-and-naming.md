# Domain Modeling and Naming

Use when a specification adds or reshapes concepts, schemas, identities, revisions, lifecycle boundaries, or shared terminology. The examples below explain modeling choices; they do not establish product behavior. Read the owning contract before assigning semantics to an existing resource or operation.

## Derive Models from Flows

Walk through representative create, continue, change, cancel, read, and resume flows before selecting schemas or tables. For each operation, identify whether it creates an identity, continues one, or records an observation; specify the owner and lifecycle boundary.

Define concepts, relationships, and authority before field lists. Separate independently changing values at the boundary that owns their lifecycle. Keep values together when they have no independent identity, lifecycle, authority, compatibility, or query value.

For example, if one configuration value may change while another must remain frozen, placing both in a single immutable snapshot is too coarse. Determine which owner accepts the change before splitting the model. Do not infer from this example that an existing Run permits configuration mutation.

Managed references, exact revisions, overrides, inline definitions, triggers, and child entry paths should converge on the same core concepts when they represent the same semantics. An additional entry path does not by itself justify a parallel model.

Persisted and public types describe domain facts. Resolution, preparation, loading, or projection stages warrant separate models only when their results have independent contract meaning. Retain snapshots when historical reconstruction or compatibility makes them meaningful.

## Canonical Terms and Types

Use one owning specification, canonical model, and term for each concept; other documents link to it. Compare meaning before consolidating names: similar fields may represent distinct authority or compatibility boundaries.

Names state what a concept is without repeating the project or module namespace. Add a qualifier only when it distinguishes real concepts at the same boundary. For implementation naming, follow [DEVELOPMENT.md](../../../../DEVELOPMENT.md#naming).

Domain suffixes such as `Ref`, `Revision`, `Request`, `Selection`, `Lock`, `State`, `Event` and `Receipt` keep the meanings defined by [Platform Data Conventions](../../../../spec/data-conventions.md#public-and-internal-naming). A reference or receipt does not confer authority unless its contract says so.

Preserve distinct identity domains in conceptual schemas even when wire encodings are strings:

```python
# Conceptual identity types, not a wire-format declaration.
class RunEvent:
    run_id: RunId
    thread_id: ThreadId
```

Do not rename stable wire fields merely to improve internal names. A terminology change that crosses public, durable, or independently released boundaries requires the owning compatibility decision.

## Version and Revision Semantics

Read [Platform Data Conventions](../../../../spec/data-conventions.md) for Foundation identity, Revision, Snapshot, and version rules, and [Platform API Conventions](../../../../spec/api-conventions.md#mutations-and-retries) for mutation preconditions.

These contracts own the primary `version` axis, the qualification of any independent secondary axis, head/Revision version agreement, and strong ETags for mutable representations. Do not invent another counter or freeze independently mutable metadata to satisfy a naming pattern. Protocols, artifacts, packages, and external systems retain their own version semantics.

## Model Review

Check that representative flows fit the identities, each separate model has independent meaning, and values change at the correct lifecycle boundary. Search for existing owners and alternate terms before adding concepts. Verify that terminology remains clear in context and that any public/durable rename preserves the accepted compatibility contract.
