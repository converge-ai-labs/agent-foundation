# Pydantic AI Foundation

## Design Position

`agent-harness` builds directly on Pydantic AI 2. Pydantic AI is the process-local Agent runtime; the Harness contributes hosting-oriented composition around its documented public APIs.

The Harness does not fork or reproduce the Agent graph, Model interface, Model profile, Capability lifecycle, Toolset composition, message codec, deferred-tool model, output contract, or usage accumulator.

## Upstream Primitive Mapping

| Pydantic AI primitive              | Harness use                                                           |
| ---------------------------------- | --------------------------------------------------------------------- |
| `Agent` and `AgentSpec`            | One authoritative Agent construction and execution path               |
| `Model` and `ModelProfile`         | Provider request/response behavior and compatibility                  |
| `ResolveModelId`                   | Thin optional bridge to fresh `ModelRunBinding`                       |
| `AbstractCapability[AgentContext]` | Reusable behavior inside the Agent loop                               |
| `CapabilityOrdering`               | Native Capability dependencies and wrapper order                      |
| Tools and Toolsets                 | Native tool schema, preparation, and dispatch                         |
| `ExternalToolset`                  | Native external/client-side deferral                                  |
| `DeferredToolRequests` and results | Native external-call and approval stop/resume values                  |
| `RunContext[AgentContext]`         | Live messages, usage, limits, capabilities, and typed dependencies    |
| `AgentRunEvents`                   | Lazy event stream, messages, usage, result, cancellation, and cleanup |
| `ModelMessage` codec               | Portable public conversation history                                  |
| `RunUsage` and `UsageLimits`       | Shared monotonic usage accumulator and native limits                  |
| `OutputSpec`                       | Validated output and output retry semantics                           |

Capability authors import these values directly from Pydantic AI. The Harness does not publish parallel aliases.

## Harness Additions

The Harness adds:

- process-local `AgentDefinition` containing native objects;
- one synchronous `HarnessBuilder` that calls `Agent.from_spec()`;
- trusted code-first plugins around the outer semantic-input-to-result boundary;
- fresh `RunBindings` and one `AgentContext` per logical run;
- an Environment lifecycle aggregate entered before input and Pydantic work, with a Host-retained controller active across the logical run;
- portable messages, Capability state, and optional portable Environment state in `HarnessState`;
- normalized process-local events and result combinations;
- exact Model-history repair and bounded interrupted-execution recovery.

These additions use public Pydantic APIs. There is no definition compiler, serialized plugin spec, Host role registry, or alternate model-resolution framework. The exact Host-supplied custom Capability type catalog exists only to authorize native `AgentSpec` reconstruction and performs no package discovery.

## Construction

```mermaid
flowchart LR
    Host[Trusted Host composition] --> Definition[AgentDefinition]
    Definition --> Plugins[Agent-bind plugins]
    Plugins --> Contributions[Capability contributions]
    Definition --> Native[Model, build-time output, Capabilities]
    Contributions & Native --> FromSpec[Agent.from_spec]
    FromSpec --> Agent[Pydantic Agent]
```

`AgentDefinition.agent` is copied and passed to `Agent.from_spec()` with `deps_type=AgentContext`, the selected Model or model name, the exact authorized custom Capability types, and the combined explicit Capabilities. Feature tools and Toolsets exist only inside their owning Capabilities. The build selects either an explicit native `OutputSpec` or native `AgentSpec.output_schema`; the latter yields `dict[str, JsonValue]`. The Harness always includes one thin `ResolveModelId` Capability and always defers eager string-model checking until run dependencies exist.

`build_code()` constructs the same `AgentDefinition` as `build()` and adds no second path.

A hosted service owns any durable schema, Preset materialization, adapter configuration, and artifact locks needed to reconstruct these Python values. Pydantic and Harness objects do not become durable documents.

## Model Mapping

A concrete native Model is used directly and optionally wrapped in `SelfHealingModel`. A string model selection reaches the thin resolver:

- with `RunBindings.model_binding`, the fresh binding returns a native Model or raises;
- without a binding, the resolver returns `None` and Pydantic continues its native inference chain.

The resolved native Model carries its own effective profile and provider adapter behavior. The Harness does not duplicate settings/profile merge logic.

Recovery ownership is intentionally split:

- Pydantic owns provider-suspended continuation and output validation retries;
- provider/client configuration owns transport retry;
- `SelfHealingModel` owns one exact replay after an effective history repair;
- the Harness coordinates bounded `ModelAttempt` recovery after recoverable model interruption.

## Plugin and Capability Mapping

A Harness plugin wraps a wider boundary than a Capability:

```mermaid
flowchart LR
    Input[Semantic input] --> Plugin[Harness plugin chain]
    Plugin --> Agent[Pydantic Agent]
    Agent --> Events[Events and result candidate]
    Events --> Plugin
    Plugin --> Result[Final candidate]
```

Plugins can also contribute ordinary `AbstractCapability[AgentContext]` instances before Agent construction. Pydantic owns those instances' Agent/run binding, ordering, Toolsets, hooks, and cleanup.

A plugin-contributed Capability resolves its fresh run-bound plugin through `AgentContext.plugins`. It does not retain a mutable Agent-bound plugin across concurrent runs.

## Execution Context Mapping

```mermaid
flowchart LR
    Bindings[RunBindings] --> Context[AgentContext]
    State[Optional HarnessState] --> Context
    Plugins[Fresh run plugins] --> Context
    Context --> RunContext[Pydantic RunContext]
    RunContext --> Capabilities[Run-bound Capabilities]
```

`AgentContext` carries the current Harness run ID, trusted Agent instance, mutable `AgentContextState`, entered `BoundEnvironment`, optional `ModelRunBinding`, immutable run-bound plugin index, immutable child collection, and non-authoritative metadata.

The same context is supplied to every `ModelAttempt` inside one logical Harness run. `RunBindings.capabilities`, one `RunUsage`, and optional `UsageLimits` are passed to every attempt under native Pydantic rules.

## Run Flow

01. Enter the fresh Environment aggregate with its paired controller non-active.
02. Restore compatible portable Environment data into already selected bindings.
03. Enter ordered Environment run extensions and activate the paired controller.
04. Invoke an optional input factory once and normalize input.
05. Create `AgentContext` from fresh bindings and copied Capability state.
06. Bind run plugins and freeze `BoundPluginContext`.
07. Start the plugin chain lazily on first iteration.
08. Run one `ModelAttempt` with a unique model-attempt ID.
09. On a recoverable model interruption, normalize public history and repeat within the total attempt budget while the Environment controller remains active.
10. On output, deferred work, cancellation, failure, or hard stop, build one terminal candidate.
11. Unwind trusted result middleware.
12. Establish the terminal fence and close all run resources before terminal delivery.

Provider-suspended continuation and deferred/HITL values are native Pydantic boundaries and never trigger the Harness `ModelAttempt` recovery loop.

## Boundary with the Host

| Concern                               | Host                                            | Harness/Pydantic                               |
| ------------------------------------- | ----------------------------------------------- | ---------------------------------------------- |
| Durable authoring schema and Presets  | Owns                                            | Receives reconstructed Python values           |
| Artifact installation and lock        | Owns                                            | Trusts supplied objects                        |
| Identity and provider policy          | Issues and evaluates                            | Propagates through typed bindings              |
| Process-local Agent loop              | Delegates                                       | Owns                                           |
| Durable execution and worker recovery | Owns                                            | Produces process-local state and result        |
| Client-side deferred execution        | Authenticates, persists, or executes externally | Produces/consumes native deferred values       |
| Usage persistence and billing         | Owns                                            | Produces Pydantic usage observations/snapshots |

## Compatibility

The repository selects a compatible Pydantic AI release and validates only documented public behavior used by the Harness. Compatibility tests cover:

- `Agent.from_spec()` with native build inputs;
- deferred string-model resolution through `ResolveModelId`;
- native Model profiles and wrapper behavior;
- Capability and Toolset composition;
- lazy `AgentRunEvents` streaming and cancellation;
- public messages, deferred values, output contracts, and usage;
- interrupted-message states used by bounded recovery.

Each Capability state version and Environment provider-state codec version is independent from the Pydantic package version and Harness envelope version.

## Trade-offs

### Direct Dependency vs. a Forked Runtime

Using Pydantic AI directly provides ecosystem compatibility and upstream fixes. The Harness must adapt to public API changes, but avoids maintaining a second Agent implementation.

### Public Native Objects vs. Serialized Abstractions

Native Models, Capability-owned tools and Toolsets, Capabilities, and code-first output types preserve full behavior. A Host cannot serialize them generically and must reconstruct them through trusted code. An object JSON Schema can remain declarative in native `AgentSpec.output_schema`.
