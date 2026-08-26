# Resource Management and Runtime Attachments

## Design Position

An `EnvironmentManager` is the small Host-facing management API for one resolved provider specification. It creates a resource, resumes an existing resource, pauses it when the provider supports suspension, destroys it, and exposes fresh runtime attachments while the resource is usable.

The Manager encapsulates vendor lifecycle differences without becoming a durable store or a second Environment operation API. A Host chooses whether to retain the returned provider state and when to invoke each lifecycle action. The Harness consumes only a fresh attachment and continues to own file, shell, process, output, port, topology, and portable Environment-state behavior.

## Resource Model

The following Python-like API is conceptual and process-local except for `EnvironmentProviderResourceState`:

```python
class EnvironmentProviderResourceState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str
    state_version: str
    data: JsonValue


class EnvironmentPauseMode(StrEnum):
    FULL = "full"
    FILESYSTEM = "filesystem"


class EnvironmentLifecycleCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    pause_modes: frozenset[EnvironmentPauseMode] = frozenset()


class EnvironmentManager(ABC):
    @property
    def lifecycle_capabilities(
        self,
    ) -> EnvironmentLifecycleCapabilities: ...

    async def create(self) -> ManagedEnvironment: ...

    async def resume(
        self,
        state: EnvironmentProviderResourceState,
    ) -> ManagedEnvironment: ...

    async def pause(
        self,
        environment: ManagedEnvironment,
        *,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState: ...

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
    ) -> None: ...


class ManagedEnvironment(AbstractAsyncContextManager["ManagedEnvironment"]):
    @property
    def state(self) -> EnvironmentProviderResourceState: ...

    def acquire_attachment(
        self,
    ) -> AsyncContextManager[EnvironmentRuntimeAttachment]: ...
```

A factory constructs one inert Manager from validated provider configuration and a fresh Host runtime context containing credential and client construction collaborators. Entering or invoking the Manager is the first effectful boundary. Provider SDK clients, subprocesses, and network calls remain lazy and explicit.

`create()` returns a usable new resource. `resume()` takes provider-owned state for an existing resource and returns it in a usable running form. If the resource is already running, resume attaches without replacing it; if it is suspended, resume performs the provider's restore/start operation. It never silently creates a different resource when the selected state is missing or incompatible.

`pause()` consumes the live resource and returns updated state after the provider confirms suspension. `FULL` asks the provider to preserve its complete resumable runtime when supported. `FILESYSTEM` preserves the provider filesystem but permits processes and memory to be discarded. An unsupported mode fails before changing the resource. `destroy()` is explicit and terminal; leaving a `ManagedEnvironment` context only disconnects local clients and maintenance tasks.

A Manager exposes only lifecycle behavior shared well enough to be dependable. Provider-specific template authoring, account administration, image building, billing, listing, and arbitrary vendor API passthrough are not added to this interface.

## Provider Resource State

`EnvironmentProviderResourceState` is the minimum provider-owned value needed to find and resume or destroy one resource. It commonly contains a container or sandbox ID plus a bounded provider generation or configuration fingerprint. It can contain private routing facts when required, but never a credential or live client.

The provider owns `state_version`, data validation, and compatibility. A Host may keep the state in memory or persist it under its own security and retention policy. Possession of the value does not grant access: every resume or destroy operation still uses current Host-supplied credentials and provider checks.

Provider resource state is not portable Harness state. It never enters `HarnessState.environment_state`, model context, Environment descriptors, tool results, or ordinary events. It also does not contain an EIP session, transfer, process handle, output reference, or `agent-envd` operation record.

## Runtime Attachments

The shared attachment union is deliberately small:

```python
@dataclass(frozen=True, slots=True)
class DirectLocalEnvironmentAttachment:
    attachment_id: str
    environment_id: str
    configuration: DirectLocalProviderConfiguration


@dataclass(frozen=True, slots=True)
class EIPEnvironmentAttachment:
    attachment_id: str
    environment_id: str
    session_source: EIPSessionSource


type EnvironmentRuntimeAttachment = (
    DirectLocalEnvironmentAttachment | EIPEnvironmentAttachment
)
```

An attachment is fresh, process-local, non-serializable, and single-use. An entered `ManagedEnvironment` issues at most one active attachment at a time. Closing the attachment releases binding-local resources but leaves the managed provider resource available for another sequential run.

The provider package imports no Harness type. The Harness adapter exhaustively converts:

- `DirectLocalEnvironmentAttachment` to a fresh Direct Local provider binding;
- `EIPEnvironmentAttachment` to a fresh EIP-backed provider binding.

Unknown attachment types fail before aggregate transfer. Docker and E2B never return vendor-native file or command attachments.

## EIP Session Sources

An EIP attachment carries an explicit session source:

```python
class EIPSessionSource(ABC):
    @abstractmethod
    def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AsyncContextManager[EIPSession]: ...
```

Each entry returns a fresh initialized low-level `EIPSession` or fails. The source owns carrier acquisition and transport authentication. Generated methods, initialization validation, operation IDs, transfers, timeout, cancellation, reconciliation, and errors remain owned by `converge-agent-envd-client` and EIP.

Supported sources are:

| Source                     | Acquisition                                                        | Protocol role                  |
| -------------------------- | ------------------------------------------------------------------ | ------------------------------ |
| Trusted process stdio      | Own private `agent-envd` stdin/stdout pipes                        | Client requests; envd responds |
| Host-dialed HTTP(S)        | Dial the dedicated EIP HTTP listener                               | Client requests; envd responds |
| Accepted reverse WebSocket | Claim an authenticated envd-initiated carrier from a Host registry | Client requests; envd responds |

For reverse WebSocket, the Host registry authenticates and bounds the accepted socket, then atomically claims the selected candidate. The low-level client wraps it and sends `initialize`. Envd remains the responder even though it opened the carrier.

A source never shares an initialized session, resumes a transfer, or automatically retries an operation whose dispatch is ambiguous. Same-generation reconnect creates a fresh EIP session. A changed daemon generation requires a fresh Harness binding revision.

## Lifecycle and Harness Flow

```mermaid
sequenceDiagram
    participant Host
    participant Manager
    participant Resource as ManagedEnvironment
    participant Harness
    participant Envd

    alt new resource
        Host->>Manager: create()
    else existing resource
        Host->>Manager: resume(resource state)
    end
    Manager-->>Host: usable ManagedEnvironment and current state
    loop sequential Harness runs
        Host->>Resource: acquire_attachment()
        Resource-->>Host: fresh single-use attachment
        Host->>Harness: adapt attachment into fresh binding
        Harness->>Envd: initialize fresh EIP session when applicable
        Harness-->>Host: result and HarnessState candidate
        Harness->>Resource: release attachment
    end
    alt retain for later
        Host->>Manager: pause(resource, mode)
        Manager-->>Host: updated provider resource state
    else remove
        Host->>Manager: destroy(resource state)
    else leave running
        Host->>Resource: disconnect local resource context
    end
```

A `ManagedEnvironment` can span sequential Harness runs. Every run still receives a fresh `EnvironmentProviderBinding`, and every EIP-backed binding receives a fresh initialized session. One built-in resource supports one active attachment/session at a time; callers that need concurrent independent runs create or resume independent resources.

Closing a Harness binding, disconnecting a `ManagedEnvironment`, pausing a resource, and destroying it are separate facts. Harness cleanup never chooses pause or destroy policy.

## Resume Semantics

Resume restores provider resources, not Harness authority:

1. the Host selects a validated provider specification and matching provider resource state;
2. `EnvironmentManager.resume()` makes that resource usable or fails explicitly;
3. the managed resource starts or validates `agent-envd` as required by the provider;
4. the resource issues a fresh attachment;
5. the Harness creates a fresh binding and EIP session;
6. the Harness restores optional portable `EnvironmentState` only after fresh binding authority exists.

A full-memory sandbox resume can preserve the in-sandbox `agent-envd` process and daemon generation, but every external connection and EIP session is still fresh. A filesystem-only resume can reboot the sandbox, so the provider restarts `agent-envd`; its daemon generation changes and no prior EIP handle, process reference, receipt, or output reference remains valid.

EIP 1.0 contributes no `EnvironmentBindingState`. Provider-managed filesystem persistence and Harness portable Environment state are separate mechanisms and must not be double-counted as session restoration.

## Failure Semantics

| Failure                                                | Outcome                                                                            |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| Create fails before a resource exists                  | No `ManagedEnvironment` is returned                                                |
| Create outcome is uncertain                            | Typed unknown outcome; caller inspects provider state before another create        |
| Resume state is missing, expired, or incompatible      | Explicit failure; no replacement resource is created                               |
| Pause fails before acceptance                          | Resource remains live when the provider can prove it                               |
| Pause outcome is uncertain                             | No claim that the resource is running or paused; inspect before another transition |
| Attachment acquisition or initialization fails         | No Harness binding is published; managed resource can remain usable                |
| EIP carrier fails after possible dispatch              | Unknown operation outcome; reconcile by operation ID                               |
| Harness binding cleanup fails                          | Harness preserves the nearest valid operation/result candidate                     |
| Managed-resource disconnect fails                      | Provider cleanup failure; no implied pause or destroy                              |
| Destroy reports not found                              | Successful absence only when the provider response is authoritative                |
| External cancellation after possible provider dispatch | Cancellation propagates with unknown outcome preserved                             |

Management calls and attachment entry have finite timeouts and preserve provider error categories needed for a caller to distinguish invalid input, unavailable service, missing resource, unsupported lifecycle action, and unknown outcome. They do not expose raw credentials, request bodies, or vendor object representations.

## Security and Dependencies

Provider credentials are supplied through a live Host collaborator and are absent from specification, resource state, attachment values, Harness state, model context, and default telemetry. Docker daemon access, E2B API credentials, and EIP session credentials remain separate authorities.

Synchronous vendor SDK calls execute through a bounded worker-thread boundary such as `anyio.to_thread.run_sync`. The Manager does not create a second thread scheduler or hide long-running work on the event loop.

## Compatibility

Provider configuration schema, Manager API, provider resource-state codec, lifecycle capabilities, attachment union, vendor SDK, EIP version, and Harness adapter are independent compatibility axes. The Harness release group aligns package APIs; provider and wire states retain their own versions.

Making attachment values reusable, treating resource-context exit as pause/destroy, silently recreating on failed resume, using vendor operations as Harness file/process fallbacks, or storing provider resource state in `HarnessState` is incompatible.

## Invariants

01. The Manager API is limited to create, resume, pause, destroy, and fresh attachment acquisition.
02. A Host chooses lifecycle actions and optional storage; the provider package owns no durable resource registry.
03. Resume returns the selected existing resource or fails; it never silently creates a replacement.
04. Provider resource state is credential-free, provider-owned, and distinct from `HarnessState`.
05. A managed resource spans sequential runs, while every attachment, binding, and EIP session is fresh and single-use.
06. Closing a binding, disconnecting a resource, pausing it, and destroying it are independent operations.
07. Full and filesystem-only pause semantics are explicit capabilities rather than inferred from provider names.
08. Docker and E2B use EIP for all Harness Environment operations.
09. Resume reestablishes provider and binding authority before portable Environment state restoration.
10. Cancellation and transport failure never convert possible side effects into non-dispatch or rollback.
