# Domain Modeling and Naming

Use this reference when a specification adds or reshapes core concepts, schemas,
identities, revisions, lifecycle boundaries, or shared terminology.

## Core Rules

### Walk through use flows before modeling

Start with representative ways a caller creates, continues, changes, cancels,
reads, and resumes the work. Use those flows to discover identity and lifecycle
boundaries before choosing resources, schemas, APIs, or tables. If a common flow
requires an exception or cannot be expressed naturally, the model is not ready.

### Define concepts before schemas

First state what the core concepts mean, which ones have independent identity,
who owns them, how they relate, and when their lifecycles begin and end. A field
list cannot repair an unclear domain model.

### Split by independent change

If one value can change while another remains valid, do not freeze them in the
same immutable boundary. If a value has no independent identity, lifecycle,
authority, compatibility, or query value, keep it inside the concept that owns it
rather than creating another resource or model.

### Keep one canonical concept

One concept has one owning specification, one canonical model, and one canonical
term. Other documents link to that owner instead of copying the schema, renaming
it, or defining a local variant.

### Model domain facts, not processing stages

Persisted and public models describe stable facts and observable contracts. Do
not turn every resolution, preparation, loading, or projection step into a domain
type. An implementation stage deserves a model only when the resulting value has
independent contract meaning.

### Normalize entry paths

Managed resources, exact revisions, overrides, inline definitions, triggers,
children, and other entry paths should converge on the same core concepts. A new
entry path should not create a parallel ontology.

### Keep names short, precise, and consistent

A name states what the concept is. Do not repeat a project or module namespace in
the type name. Use one term for one concept and one meaning for each term. Give
suffixes such as `Id`, `Ref`, `Revision`, `Request`, `Selection`, `Lock`, `State`,
`Event`, and `Receipt` stable meanings.

## Representative Cases

### Bad: schemas come before the common flows

Suppose the first schema assumes one Thread can accept work only while idle and
one Run can contain only one input. Queueing another request or steering active
work then requires exceptions that the original identities cannot express.

Good: write the flows first and derive the model from their identity behavior.

```text
start work       -> new Run
submit while busy -> new queued Run
steer active work -> same Run, new RunInput
replace a lost worker -> same Run, new RunAttempt
```

The exact names are domain-specific; the reusable rule is to settle whether each
operation continues or creates an identity before designing the schema.

### Bad: one immutable object owns independently changing values

```python
class Run:
    agent_revision_id: AgentRevisionId
    model_snapshot: ModelExecutionSnapshot
    skill_keys: tuple[str, ...]
    environment: EnvironmentExecutionConfig
```

If the Agent, model, or Skills can change during the Run while the Environment
cannot, this boundary is too coarse.

Good: assign each value to the smallest concept whose lifecycle actually owns it.

```python
class Run:
    environment: EnvironmentExecutionConfig


class RunInput:
    agent: AgentSelection
    effective_model: ModelExecutionConfig
    skill_keys: tuple[str, ...]
```

### Bad: an intermediate value becomes an unnecessary model

```python
class ModelExecutionSnapshot:
    # No identity, API, independent lifecycle, or reuse.
    ...


class Run:
    model_snapshot: ModelExecutionSnapshot
```

Good: store the resolved value directly in the input or state that owns it. Keep
a separate snapshot type only when the snapshot itself has independent contract
meaning.

### Bad: type names repeat their namespace

```python
class FoundationAgentSkillSelectionRequest: ...
class FoundationSkillRevisionLock: ...
```

Inside the Foundation Skill module, `Foundation` repeats information already
provided by the namespace.

Good:

```python
class AgentSkillSelectionRequest: ...
class SkillRevisionLock: ...
```

Add a domain qualifier only when it distinguishes this concept from another real
concept at the same boundary.

### Bad: one concept uses several terms

```text
provider_key
provider_type
```

If both fields identify the same provider catalog entry, choose one canonical
term and use it across schemas, prose, APIs, events, and SDKs.

### Bad: one term carries several meanings

```text
version = mutable resource compare-and-swap version
version = immutable revision ordinal
version = external package release
```

Good: use distinct names such as `version`, `revision_number`, and
`package_version` when these axes can vary independently.

### Bad: another document copies a shared concept

```python
class WorkspaceSecretCredential: ...
class WorkspaceSecretEnvironmentCredential: ...
```

When these express the same Secret selection and eligibility contract, one owner
defines the shared concept and both consumers reference it. Do not preserve two
models merely because they were introduced by different features.

### Bad: conceptual identities become untyped strings

```python
class RunEvent:
    run_id: str
    thread_id: str
```

Good:

```python
class RunEvent:
    run_id: RunId
    thread_id: ThreadId
```

Use distinct identity types in conceptual schemas so the contract preserves
identity domains even when the wire encoding is a string.

## Review Questions

Before accepting a model or name, ask:

1. Have representative create, continue, change, cancel, read, and resume flows been walked through before choosing the schemas?
2. Can each core concept be explained without referring to a table, endpoint, or Worker step?
3. Can any two fields change independently, and if so, are they separated at the correct lifecycle boundary?
4. Does every separate model have independent contract meaning?
5. Does another document already own this concept or use another name for it?
6. Would the name remain clear without its surrounding heading, while avoiding namespace repetition?
