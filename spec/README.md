# Open-Source Agent Platform Overview

## Platform Definition

Agent Foundation is an open-source foundation for embedding Agents or hosting them as durable services. It provides reusable process-local execution, Environment, protocol, and hosting semantics while leaving product experience, business workflow, and infrastructure vendors to adopters.

The platform consists of:

- `agent-harness`, distributed as `converge-agent-harness`, for code-first Pydantic AI execution;
- `agent-stream-protocol`, distributed as `converge-agent-stream-protocol`, for shared AG-UI projection and validation;
- `agent-ui`, distributed as `converge-agent-ui`, for local sessions and WebUI/TUI interaction;
- `agent-envd`, distributed as `converge-agent-envd`, for Environment Interaction Protocol operations;
- `converge-agent-envd-client`, the generated low-level Python EIP client;
- `foundation-service`, distributed as `converge-foundation-service`, for optional durable hosting;
- Foundation Service SDKs for typed access to the hosted `/api` boundary;
- `agent-foundation`, a cross-platform remote CLI built above the Foundation Rust SDK.

An application can embed the Harness directly, install Agent UI for a local interactive Host, use Foundation Service through an SDK or the CLI, or replace providers through documented typed and protocol boundaries.

## Architecture

```mermaid
flowchart TB
    subgraph Product[Product or internal service]
        API[Product API and experience]
        Policy[Authentication and business policy]
    end

    subgraph Service[foundation-service]
        Control[Control plane]
        ResourceIAM[Organization, Workspace, and resource authorization]
        Definitions[Host-owned definition revisions]
        Lifecycle[Durable Executions and Attempts]
        Worker[Execution worker]
        Reconstruct[Trusted reconstruction adapters]
    end

    subgraph LocalUI[agent-ui]
        AppService[Local application service]
        Sessions[Local sessions]
        WebUI[Bundled WebUI]
        TUI[Terminal UI]
    end

    subgraph FoundationClients[Foundation clients]
        FoundationCLI[agent-foundation CLI]
        RustSDK[Foundation Rust SDK]
    end

    subgraph Harness[agent-harness]
        Definition[Process-local AgentDefinition]
        Builder[HarnessBuilder]
        Plugins[Trusted Harness plugins]
        Bindings[RunBindings]
        Context[AgentContext]
        Run[HarnessRunStream]
        State[HarnessState]
    end

    StreamProtocol[agent-stream-protocol]

    subgraph Environment[Environment layer]
        Bound[BoundEnvironment]
        Local[Direct local operators]
        EIPClient[converge-agent-envd-client]
        EIP[EIP]
        Envd[agent-envd]
    end

    subgraph External[External systems]
        Models[Model providers]
        Tools[Tool and connector providers]
        Stores[Durable stores]
        Identity[Identity, policy, credentials]
        Clients[External client-tool executors]
        OTel[OpenTelemetry]
    end

    Product --> Service
    FoundationCLI --> RustSDK --> Control
    Product -. embedded .-> Definition
    Product -. local interactive .-> AppService
    AppService --> Sessions
    AppService --> Definition
    AppService --> StreamProtocol --> WebUI & TUI
    Control --> ResourceIAM
    ResourceIAM --> Definitions --> Lifecycle --> Worker
    Definitions --> Reconstruct --> Definition
    Definition --> Builder --> Plugins
    Worker --> Bindings
    Identity --> Bindings
    Plugins & Bindings --> Context --> Run --> State
    Run --> Models & Tools
    Run -. deferred calls .-> Clients
    Context --> Bound
    Bound --> Local
    Bound --> EIPClient --> EIP --> Envd
    Service --> Stores
    Run -. telemetry .-> OTel
```

Dependency direction is one-way: Hosts embed the Harness; Agent UI and hosted transports consume Agent Stream Protocol above public Harness observations; the Harness uses provider and Environment boundaries; providers do not import Host lifecycle or presentation types. Foundation clients call only the public service `/api` boundary, and the Foundation CLI consumes the Rust SDK rather than implementing a second transport client.

## Component Responsibilities

| Component               | Owns                                                                                                                                                                                                          | Does not own                                                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `agent-harness`         | Process-local Agent construction, narrow plugin configuration/loading, trusted plugins, run context, recovery, execution, results, and continuation state                                                     | Durable Agent authoring schemas, local sessions, presentation protocols, worker lifecycle, delivery, billing |
| `agent-stream-protocol` | Standard AG-UI projection profiles, validation, ordering, replay envelopes, and namespaced extension policy                                                                                                   | Agent execution, Host acceptance, sessions, durable events, HTTP/SSE, or rendering                           |
| `agent-ui`              | Local profiles, sessions, foreground orchestration, process-local background children, application service, WebUI, and TUI                                                                                    | Distributed execution, multi-tenant authorization, or another Agent loop                                     |
| `agent-envd-client`     | Generated EIP control/data models, codecs, stubs, async file transfer, and bounded transport/session runtime                                                                                                  | Harness routing, product-user authorization, provider provisioning, Host lifecycle                           |
| `agent-envd`            | Client-neutral EIP Environment hosting, raw file transfer, operations, receipts, disk-backed command output, daemon generation, and native command containment                                                | Agent loop, browser/product authentication, arbitrary URL fetch, durable execution, model policy             |
| Foundation SDKs         | Language-typed access to the public Foundation Service `/api` contract                                                                                                                                        | Service internals, product policy, or durable lifecycle authority                                            |
| `agent-foundation`      | Cross-platform command-line interaction with public Foundation Service operations through the Rust SDK                                                                                                        | A second HTTP client, service process management, persistence, queues, migrations, or infrastructure control |
| `foundation-service`    | Organization and Workspace resource authorization, Host-owned definition/Presets/revisions, reconstruction locks, Executions/Attempts, client tools, APIs, events, usage records, and optional web projection | Pydantic Agent loop, Python object serialization, client-side effects, provider-native state meaning         |
| Product                 | Product-specific caller experience, upstream identity integration, business policy outside Foundation resources, and final delivery                                                                           | Foundation resource authorization, Harness internals, and provider implementation                            |

## Harness Foundation

The Harness is built directly on Pydantic AI 2:

- `AgentDefinition` is an immutable process-local Python value containing native `AgentSpec`, one build-time explicit or schema-derived output contract, a Model/model name, top-level Capabilities, plugins, and recovery configuration;
- Capability is the only top-level feature-behavior plane; each feature Capability owns its tools, Toolsets, instructions, settings, and hooks;
- `HarnessBuilder` resolves an explicit or disabled-by-default ambient plugin Build Context, creates fresh configured instances, binds all trusted plugins, authorizes custom Capability types, and calls `Agent.from_spec()` once;
- `RunBindings` supplies fresh Agent instance, Environment, optional `ModelRunBinding`, run Capabilities, and metadata;
- one logical Harness run owns one context, Environment, plugin graph, state coordinator, usage accumulator, and public run ID;
- bounded model recovery can start several Pydantic inner attempts with unique inner run IDs inside that logical run;
- `HarnessState` carries public messages, detached Capability JSON namespaces, and optional portable Environment backend data; desired topology, provider incarnation envelope, and launch payload remain Host-owned;
- Pydantic AI owns native Model profiles, transport/output retries, provider-suspended continuation, deferred external calls/approvals, Toolsets, messages, events, and usage.

Harness middleware plugins are trusted concrete objects, and the Harness does not compile serialized Agent definitions. It owns a narrow versioned preferred YAML or supported JSON plugin document and Build Context that may, when explicitly enabled, select `converge_agent_harness.plugins` factories and append fresh concrete plugins during each definition build. `converge_agent_harness.environments` remains a caller-selected catalog because Environment topology and lifecycle are run-scoped. A Host owns serializable Agent schemas, artifact locks, package trust, and durable execution; package presence and Harness import alone never enable behavior or supply an arbitrary import target.

The complete design is indexed in [agent-harness/README.md](agent-harness/README.md).

## Local Agent Interaction

Agent UI is a local single-user Host above the Harness. It pins resolved Agent-profile snapshots, selects complete `HarnessState` checkpoints, manages process-local asynchronous child jobs, and exposes one application service through a default bundled WebUI or a TUI. Both surfaces consume the same Agent Stream Protocol AG-UI projection; neither interprets private Harness events or owns a second session model. Its application service can optionally accept envd-initiated reverse-WebSocket attachments and, after explicit local Host selection, publish them into fresh or active Harness Environment topology without giving browser state protocol or provider authority.

`converge-agent-harness` and `converge-agent-stream-protocol` form the Harness release group. One `release/harness-v<version>` tag assigns the same version to both distributions, and the published Stream Protocol artifact requires that exact Harness version. Agent UI releases independently through `release/agent-ui-v<version>` and its published artifact pins both libraries to one reviewed Harness release version. Source checkouts continue to resolve unversioned package dependencies from the shared uv workspace. Each tag version is a stable `X.Y.Z` identity or an RC `X.Y.Z-rc.N` identity as defined by [repository release automation](repository-model.md#release-automation). Source directories omit the distribution prefix (`packages/agent-*`), while Python distribution names use `converge-` and import packages use `converge_`.

The complete local Host design is indexed in [agent-ui/README.md](agent-ui/README.md), and the shared presentation protocol is indexed in [agent-stream-protocol/README.md](agent-stream-protocol/README.md).

## Recovery Boundaries

| Concern                             | Owner                                      |
| ----------------------------------- | ------------------------------------------ |
| Provider-suspended continuation     | Pydantic AI                                |
| Provider transport retry            | Provider/client and Pydantic `RetryConfig` |
| Exact provider-history repair       | Harness `SelfHealingModel`                 |
| Interrupted model semantic attempts | Harness run coordinator                    |
| Worker crash and durable recovery   | Host                                       |
| External side-effect reconciliation | Provider and Host                          |

Recovery never converts missing evidence into rollback or exactly-once success. Interrupted tool history records that the operation may have partially or fully completed and directs the next model to inspect state before retrying.

## Environment Foundation

Environment is a Harness-owned run lifecycle resource, not a Capability. `BoundEnvironment` gives trusted code and optional model-facing Capabilities a stable run- and Identity-bound facade over provider-neutral file, shell, process, and port operations. Its paired Host-retained controller activates after initial portable-state restore and supports atomic add, refresh, and removal throughout the active logical run. Direct local and EIP-backed implementations are first-class peers.

The Harness adapts EIP through `converge-agent-envd-client`; other trusted consumers such as product file gateways can use that client independently. `agent-envd` carries JSON-RPC control and raw bidirectional file transfer over trusted stdio or an envd-initiated reverse WebSocket, and owns daemon-generation operation/receipt evidence, process handles, disk-backed command output with explicit-offset reads, session-scoped transfers, and Linux/macOS/Windows command containment. Product/browser authentication stays at the gateway/control service, and short-lived envd attachment credentials never reach the browser. The Host selects providers and desired topology and materializes trusted process-local bindings. The Harness imports no vendor provisioning API, and Foundation Service does not persist a generic Sandbox resource.

Provider-defined portable backend data can enter only the explicit `HarnessState.environment_state` field after fresh bindings are selected. Provider resource-incarnation evidence and optional launch/reattachment payload remain in a separate encrypted Host envelope. Live clients, sockets, credentials, process handles, readiness, controllers, and provider authority do not become Harness state. Optional `DynamicEnvironmentCapability` composes File/Shell tools with dynamic model context but owns neither provider lifecycle nor state.

## Foundation Client Surfaces

Foundation Service clients operate only through the public `/api` namespace. The language SDKs own typed transport and service-contract mapping. The `agent-foundation` executable is a user-facing composition layer above the Rust SDK and does not duplicate HTTP serialization, authentication transport, retries, or service models.

A CLI network command and its backing SDK operation enter the platform together with the corresponding real service API and end-to-end behavior. The CLI does not reserve unsupported commands as placeholders. Service-process startup, migrations, databases, Redis, queues, containers, Kubernetes, and other operator internals remain owned by Foundation Service deployment surfaces rather than the remote client.

The CLI source, workspace isolation, validation, and binary-only release channel are defined by the [repository model](repository-model.md).

## Hosted Service Foundation

Foundation Service adds durability without changing Harness execution semantics:

```mermaid
flowchart LR
    Ingress[API or webhook] --> Control[Control plane]
    Control --> Durable[Definitions and Executions]
    Durable --> Queue[Scheduling]
    Queue --> Worker[Execution worker]
    Worker --> Reconstruct[Trusted adapters]
    Reconstruct --> Harness[agent-harness]
    Harness --> Candidate[Events, result, state, usage]
    Candidate --> Durable
```

Foundation definitions are Host-owned serializable documents, not Harness `AgentDefinition` wire values. A worker verifies exact dependency/artifact locks, reconstructs native Pydantic/Harness objects, resolves operator-approved Environment providers, materializes current desired topology from encrypted launch-envelope entries, durably advances an unrepresented replacement resource's binding/topology incarnation revisions, and supplies fresh `RunBindings` to the same public API as an embedded application. The worker retains the paired Environment controller only for that active logical run.

One durable Foundation Attempt starts one logical Harness run. Internal Harness model attempts are not durable Attempt generations. Authorized desired Environment topology can advance during that run and is reconciled through the retained controller with separate effective publication. Worker or lease loss creates a new fenced Attempt, fresh provider bindings, and a fresh Harness run from authoritative selected Host and Harness state.

Client-side tools use native Pydantic deferred values. Foundation durably commits pending calls and approvals, authenticates external feedback, and starts a later run with fresh bindings. Asynchronous children use independent Executions and result-delivery ledgers rather than Pydantic deferred spawn calls.

The hosted service design is indexed in [Foundation Service](foundation-service/README.md). Its [architecture](foundation-service/00-overview.md), [execution lifecycle](foundation-service/03-executions-attempts-and-checkpoints.md), and [worker recovery contract](foundation-service/04-scheduling-workers-and-recovery.md) own the durable semantics summarized here.

## Deployment Profiles

| Profile             | Persistence and coordination                        | Execution                                   |
| ------------------- | --------------------------------------------------- | ------------------------------------------- |
| Embedded            | Application-selected                                | Harness in product process                  |
| Local Agent UI      | Atomic local session store and process coordination | Harness plus process-local child jobs       |
| Minimal service     | SQLite and in-memory coordination                   | Control and execution together              |
| Distributed service | PostgreSQL durable authority and Redis coordination | Separately scalable control/execution roles |

Coordination streams and queues do not become durable lifecycle authority.

## Identity and Version Boundaries

The platform-wide identity, versioning, naming, ownership, and compatibility rules are defined by [Platform Data Conventions](data-conventions.md). Concrete subsystems own their object catalogs, schemas, and explicitly scoped or external identifiers within those rules.

The platform distinguishes:

- Organization and Workspace resource scope;
- caller/actor identity;
- stable Agent workload Identity and Agent instance;
- Host-owned immutable definition revision and dependency locks;
- process-local Harness run and inner Pydantic attempt;
- Host durable Execution and Attempt;
- Environment identity and generation;
- credential binding and invocation grant.

A Host definition revision contains only serializable Host data and exact locks. It contains no plugin/Capability class, native Model, Toolset, output Python type, callable, client, plaintext credential, or process-local object. An execution reconstructs those values without mutating the selected revision.

## Service API Boundaries

Foundation-owned resource-oriented JSON APIs and their first-party SDKs follow [Platform API Conventions](api-conventions.md). The shared contract owns HTTP resource shape, JSON representation, pagination, errors, mutation safety, and compatibility. Process-local APIs, EIP, Agent Stream Protocol profiles, provider APIs, and external webhook schemas retain their defining contracts.

## Extension Model

```mermaid
flowchart LR
    HostConfig[Host-owned Agent configuration] --> Adapter[Trusted reconstruction adapter]
    Adapter --> DirectPlugin[Direct Harness plugin]
    PluginConfig[Harness plugin document] --> Builder[HarnessBuilder]
    Builder --> ConfiguredPlugin[Configured Harness plugin]
    Adapter --> Capability[Pydantic Capability]
    Adapter --> Native[Model, tool, or Toolset]
    RunAuthority[Fresh RunBindings] --> Capability
    RunAuthority --> Environment[Environment lifecycle resource]
    Adapter --> RunExtension[Environment run extension]
    RunExtension --> Environment
    DirectPlugin & ConfiguredPlugin --> Harness[Harness run]
    Capability & Native --> Agent[Pydantic Agent]
    Environment --> Harness
    Agent --> Provider[Feature provider]
```

Installed plugins and native objects are trusted in-process code. Harness plugin, Environment provider, and Environment run-extension package presence is only availability; an operator explicitly enables or selects the relevant key before import/use. Factory-produced and directly constructed objects enter the same concrete composition path for their extension kind. Untrusted or independently governed behavior belongs behind feature-specific protocols. The core defines no universal remote-plugin or package-installation system.

## Observability and Cost

Pydantic AI instrumentation owns Agent/model/tool spans. Harness features add spans only for Harness-owned context, plugins, recovery, state, Environment, and delegation work. Foundation adds durable lifecycle, queue, child, client-delivery, and connector spans.

`RunUsage` is a process-local accumulator. Hosts own deduplication, cross-run aggregation, pricing revisions, durable usage records, budgets, billing, and payment.

## Completion Boundaries

```mermaid
flowchart LR
    Accept[Host accepts input] --> Run[Harness logical run]
    Run --> Candidate[Harness terminal result]
    Candidate --> Commit[Host durable commit]
    Commit --> Deliver[External delivery]
    Run -. projection .-> Telemetry[Telemetry]
```

Acceptance, inner attempt completion, Harness terminal delivery, Host durable commit, usage recording, telemetry export, external delivery, billing, and payment are independent facts.

## Design Principles

01. Reuse Pydantic AI, OpenTelemetry, databases, streams, and provider ecosystems.
02. Keep one authority for every durable fact.
03. Preserve native Python composition inside the execution process.
04. Keep durable Host schemas outside the Harness library.
05. Bind Identity and current authority freshly at the Host boundary.
06. Keep process-local continuation separate from durable lifecycle state.
07. Preserve unknown side effects and require provider evidence for safe replay.
08. Use optional typed packages and protocols instead of a universal extension framework.
09. Use the same Harness API in embedded and hosted modes.
10. Add enterprise behavior through the same boundaries rather than forks.

## Specification Set

| Area                                  | Document                                                                                                                               |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Repository content and workflow model | [repository-model.md](repository-model.md)                                                                                             |
| Platform data conventions             | [data-conventions.md](data-conventions.md)                                                                                             |
| Platform API conventions              | [api-conventions.md](api-conventions.md)                                                                                               |
| Harness catalog                       | [agent-harness/README.md](agent-harness/README.md)                                                                                     |
| Harness architecture                  | [agent-harness/00-overview.md](agent-harness/00-overview.md)                                                                           |
| Harness definition/build              | [agent-harness/03-agent-definition-and-build.md](agent-harness/03-agent-definition-and-build.md)                                       |
| Harness plugins                       | [agent-harness/05-plugin-system.md](agent-harness/05-plugin-system.md)                                                                 |
| Harness execution and recovery        | [agent-harness/06-execution-context-and-lifecycle.md](agent-harness/06-execution-context-and-lifecycle.md)                             |
| Harness state                         | [agent-harness/10-snapshot-and-resume.md](agent-harness/10-snapshot-and-resume.md)                                                     |
| Harness public API                    | [agent-harness/14-public-api-and-packaging.md](agent-harness/14-public-api-and-packaging.md)                                           |
| Harness model/output boundary         | [agent-harness/16-input-model-and-output.md](agent-harness/16-input-model-and-output.md)                                               |
| Agent Stream Protocol catalog         | [agent-stream-protocol/README.md](agent-stream-protocol/README.md)                                                                     |
| AG-UI projection contract             | [agent-stream-protocol/00-overview.md](agent-stream-protocol/00-overview.md)                                                           |
| Agent UI catalog                      | [agent-ui/README.md](agent-ui/README.md)                                                                                               |
| Agent UI architecture and sessions    | [agent-ui/00-overview.md](agent-ui/00-overview.md), [agent-ui/02-local-sessions-and-state.md](agent-ui/02-local-sessions-and-state.md) |
| agent-envd catalog                    | [agent-envd/README.md](agent-envd/README.md)                                                                                           |
| EIP architecture and protocol         | [agent-envd/00-overview.md](agent-envd/00-overview.md), [agent-envd/02-eip-protocol.md](agent-envd/02-eip-protocol.md)                 |
| Foundation Service catalog            | [foundation-service/README.md](foundation-service/README.md)                                                                           |
| Foundation Service architecture       | [foundation-service/00-overview.md](foundation-service/00-overview.md)                                                                 |
| Foundation resource authorization     | [foundation-service/01-resource-scope-and-authorization.md](foundation-service/01-resource-scope-and-authorization.md)                 |
| Foundation Agent revisions            | [foundation-service/02-agent-revisions-and-reconstruction.md](foundation-service/02-agent-revisions-and-reconstruction.md)             |
| Foundation execution lifecycle        | [foundation-service/03-executions-attempts-and-checkpoints.md](foundation-service/03-executions-attempts-and-checkpoints.md)           |
| Foundation scheduling and recovery    | [foundation-service/04-scheduling-workers-and-recovery.md](foundation-service/04-scheduling-workers-and-recovery.md)                   |
| Foundation deferred actions           | [foundation-service/05-deferred-actions-and-children.md](foundation-service/05-deferred-actions-and-children.md)                       |
| Foundation Environment lifecycle      | [foundation-service/06-environment-provider-lifecycle.md](foundation-service/06-environment-provider-lifecycle.md)                     |
| Foundation events and usage           | [foundation-service/07-events-usage-and-delivery.md](foundation-service/07-events-usage-and-delivery.md)                               |
