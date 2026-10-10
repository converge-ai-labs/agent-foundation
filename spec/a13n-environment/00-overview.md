# Environment Architecture

## Design Position

`a13n-environment` is a Python capability library for operating one environment. Its source lives in `packages/a13n-environment/a13n_environment`, with distribution name `a13n-environment` and import namespace `a13n_environment`. Environment Provider definitions and implementations live in this package. Each Provider supplies backend management, Environment connectors, and Environment executions as applicable, with separate responsibilities for target lifecycle and operation access.

| Layer                      | Responsibility                                                                                                                                       |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Environment library        | Single-target contracts, typed state/results/errors, management primitives, execution connections, vendor operations, and cleanup of owned resources |
| Service                    | Authorization, durable instance state, operation leases, active-use coordination, renewal scheduling, and retirement policy                          |
| Harness                    | Opening and closing Environment execution scopes, Run-local mounts, paths, permissions, tools, events, and model context                             |
| Harness UI and other Hosts | Their own selection, state storage, lifecycle policy, and composition without requiring Service                                                      |

The library has no Agent loop, model tools, mount identity, database, global scheduler, or general lifecycle orchestration framework. Its management API performs explicit operations; consumers decide when and under which authority to call them.

## Dependencies

```mermaid
flowchart TD
    Service --> Harness
    Service --> Environment[a13n-environment]
    UI[Harness UI] --> Harness
    UI --> Environment
    Harness --> Environment
    Other[Other Python consumers] --> Environment
    Environment --> Native[Native vendor APIs]
    Environment --> EIP[a13n-envd-client]
    EIP -->|EIP protocol| Envd[a13n-envd daemon]
```

The shared package imports neither Harness nor its Hosts. Provider metadata and public contracts load without importing optional vendor SDKs. Environment-owned configuration, authentication metadata, errors, and transport contracts are usable without Harness; Harness-specific plugin selection and endpoint policies enter through Host composition. Other Provider domains remain under the [Harness Provider subsystem](../a13n-harness/22-provider-subsystem.md).

Envd-backed Environment Providers implement Environment connectors and Environment executions over `a13n-envd-client`. The client owns client-side EIP transport and Session behavior; the Envd daemon serves EIP and owns server-side execution resources. Native Environment Providers use local OS or vendor APIs without requiring Envd. Extraction preserves both operation routes and the EIP protocol. Source workspace and publication conventions follow the [Repository Model](../repository-model.md#repository-surfaces).

## Management and Execution

```mermaid
sequenceDiagram
    participant Host
    participant Provider as EnvironmentProvider
    participant Harness
    participant EnvironmentConnector
    participant Execution as EnvironmentExecution
    Host->>Provider: Explicit create or start when required
    Provider-->>Host: EnvironmentState and observed outcome
    Host->>Host: Persist outcome under current authority
    Host->>Provider: execution_connector(state, options)
    Provider-->>Host: Fixed-target EnvironmentConnector
    Host->>Harness: EnvironmentConnector and mount policy
    Harness->>EnvironmentConnector: open()
    EnvironmentConnector-->>Harness: Ready EnvironmentExecution
    Harness->>Execution: Check and execute operations
    Execution-->>Harness: Results or failure evidence
    Harness->>Execution: close()
    Host->>Provider: Independently scheduled keepalive, stop, or destroy
```

Each Provider implements management and execution with separate internal objects and construction paths. Common configuration, identity validation, and transport support do not carry both roles. Management and execution can run in different processes. The Host carries portable state between them, not a shared live client. An Environment connector can open only its selected target and has no target-management methods. Opening, checking, and reconnecting an Environment execution never create, resume, replace, or renew that target.

Opening a scope, completing a command, closing a scope, and retiring a target are separate outcomes. A Run ending does not authorize target destruction, and an execution connection does not guarantee continued target survival. [The shared contract](01-environment-contract.md) owns cancellation, uncertain outcomes, reconnection, and resource lifetime.
