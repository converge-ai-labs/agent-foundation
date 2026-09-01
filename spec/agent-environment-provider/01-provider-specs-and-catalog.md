# Provider Specifications and Catalog

## Design Position

An Environment Provider is selected by a namespaced key and one versioned serializable configuration. Configuration expresses desired behavior; it does not contain current backing-target identity, credentials, clients, endpoints resolved at runtime, or process-local authority.

The catalog maps a trusted key to an inert `EnvironmentProvider`. The Provider validates configuration and constructs fresh `Environment` instances from optional `EnvironmentState` and explicit runtime collaborators. Discovery and construction perform no external I/O.

## Serialized Configuration

A provider configuration envelope has this conceptual shape:

```python
class EnvironmentProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str
    schema_version: str
    configuration: JsonValue
```

The envelope is a shared serialized boundary. A Provider owns the exact model for `configuration` at each `schema_version`.

Rules:

1. `provider_key` is a bounded lowercase namespaced key such as `a13n.docker`.
2. `schema_version` is non-blank and belongs to the configuration schema, not the state codec.
3. `configuration` is canonical JSON and rejects unknown fields through the provider model.
4. Configuration contains no credential, live client, transport session, provider target ID, resolved endpoint, PID, operation receipt, or Host record identity.
5. Paths and resource references are validated by the provider. Relative process-local interpretation is not permitted for persisted paths unless the provider schema defines an explicit portable base.
6. Validation is deterministic and performs no filesystem, daemon, network, subprocess, or provider API I/O.

A Host can store its own definition revision around this envelope. That outer record may own user names, policy, sharing, and lifecycle settings, but those values do not become Provider configuration unless the provider contract needs them to define the target.

## Provider Contract

```python
class EnvironmentProvider(ABC):
    @property
    def key(self) -> str: ...

    @property
    def configuration_versions(self) -> frozenset[str]: ...

    def validate_configuration(
        self,
        *,
        schema_version: str,
        value: JsonValue,
    ) -> BaseModel: ...

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment: ...
```

The exact language API may use typed generic runtime values, but these semantics are fixed:

- The Provider is inert after construction.
- `validate_configuration()` performs pure parsing, normalization, and deterministic validation.
- `create_environment()` performs no external I/O and returns a fresh single-use adapter.
- The state is either `None` or has the same `provider_key`. Provider-specific codec validation can occur during construction, but target validation and external observation occur only during explicit Environment operations.
- Runtime collaborators are fresh process-local trusted values. They can include credential sources, a Docker engine boundary, a bootstrap store, an EIP transport factory, or a local runtime allocator.
- Runtime collaborators and configuration are retained only by the resulting process-local Environment. They are never copied into `EnvironmentState`.
- A Provider never stores durable current state, chooses retention, or associates Threads.

There is no separate Provider factory entity. Catalog loading creates an `EnvironmentProvider` directly through the trusted entry point. There is no lifecycle Provider, Resource, attachment, or binding layer between Provider and Environment.

## Catalog

The package owns one explicit catalog:

```python
class EnvironmentProviderCatalog:
    def register(self, provider: EnvironmentProvider) -> None: ...

    def resolve(self, provider_key: str) -> EnvironmentProvider: ...

    def load_entry_points(
        self,
        *,
        enabled_keys: Collection[str],
    ) -> None: ...
```

The built-in keys are:

| Key                 | Target                                                            |
| ------------------- | ----------------------------------------------------------------- |
| `a13n.direct-local` | One Host-selected local root using direct operating-system access |
| `a13n.local-envd`   | One Host-selected workspace served by a fresh local envd process  |
| `a13n.docker`       | One Docker container running envd                                 |
| `a13n.e2b`          | One E2B sandbox running envd                                      |

Third-party Providers register under the `a13n_environment_provider.providers` entry-point group. One entry point contributes exactly one Provider. Entry-point names and `provider.key` must match.

Duplicate keys, malformed keys, import failures, wrong object types, and Provider-construction failures are explicit catalog errors. The catalog does not catch such failures and continue with a partial ambiguous selection.

## Selection and Authorization

Catalog presence does not authorize use. A Host:

1. selects an allowed key from trusted definition or deployment configuration;
2. resolves the Provider from an allowlisted catalog;
3. validates the exact configuration version and payload;
4. resolves current authoritative state and fresh runtime collaborators;
5. calls `create_environment()`;
6. passes the Environment to Harness or invokes Host-only warmup/destroy behavior.

Model content, imported Harness state, a package installed in the environment, or an arbitrary entry-point key cannot select a Provider or supply runtime collaborators.

A state value cannot retarget configuration. The Provider validates that its opaque state payload is compatible with the selected configuration. A mismatched Provider key, configuration fingerprint, target metadata, or immutable identity fails rather than being adopted or rewritten.

## Configuration Evolution

Configuration `schema_version` and `EnvironmentState.state_version` evolve independently.

- A new configuration version changes desired configuration syntax or semantics.
- A new state version changes the provider-owned re-entry payload codec.
- Supporting a new configuration version does not imply accepting old state versions.
- Supporting a state migration does not authorize changing desired configuration.
- Providers reject unsupported versions explicitly.
- Hosts migrate stored configuration or state only through an explicit provider-owned migration boundary. Entry never silently rewrites incompatible data.

Configuration normalization produces a stable fingerprint when a provider needs to prove that state belongs to the selected desired configuration. The fingerprint is compatibility evidence, not authorization or target identity.

## Failure Semantics

| Failure                                       | Behavior                                                               |
| --------------------------------------------- | ---------------------------------------------------------------------- |
| Unknown or disabled Provider key              | Fail before configuration validation or runtime resolution             |
| Duplicate catalog key                         | Fail catalog construction                                              |
| Unsupported configuration version             | Fail with supported versions and no provider effects                   |
| Invalid configuration payload                 | Fail with bounded field diagnostics and no provider effects            |
| Mismatched state Provider key                 | Fail before Environment construction                                   |
| Invalid state JSON or unsupported codec       | Fail during deterministic state validation                             |
| Missing runtime collaborator                  | Fail Environment construction or explicit entry before target mutation |
| Entry-point import or Provider creation fails | Fail catalog loading; do not silently omit an enabled Provider         |

Errors never expose credentials, bearer URLs, Docker socket details, raw Host paths outside safe configuration diagnostics, or native exception text to model-facing surfaces.

## Compatibility

Provider keys are stable serialized discriminators. A key changes only for a semantically distinct Provider family. Configuration and state version changes follow their explicit compatibility boundaries.

`EnvironmentProvider`, `Environment`, and `EnvironmentState` are the only shared lifecycle API. The pre-release removal of Provider factories, Resources, attachments, Provider bindings, lifecycle capability matrices, and pause APIs is a direct cut with no compatibility aliases.

## Invariants

01. Provider resolution is explicit and allowlisted.
02. Configuration validation and Environment construction perform no external I/O.
03. Provider configuration contains desired behavior only.
04. Current backing-target identity belongs in `EnvironmentState`, not configuration.
05. Credentials and live collaborators remain process-local.
06. Every `create_environment()` call returns a fresh single-use Environment.
07. State cannot select another Provider or retarget incompatible configuration.
08. Catalog availability never grants Agent authority.
09. Configuration and state versions are independent explicit contracts.
10. No separate Provider factory, Resource, attachment, or binding entity exists.
