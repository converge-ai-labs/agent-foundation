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

## Host Backend Configuration

A configured Host Provider separates backend access from the desired environment recipe. The same implementation supplies typed `provider_configuration_model` and optional `credential_model` definitions for backend settings and write-only credentials. Hosts derive schemas and validate those values through these models, persist them under their own resource authority, and supply fresh validated runtime collaborators. The recipe continues to use `configuration_versions` and `validate_configuration()` below. Embedded Hosts can supply equivalent typed collaborators directly without creating service resources. No Foundation resource or credential-storage model enters this package.

## Provider Contract

```python
class EnvironmentProvider(ABC):
    @property
    def key(self) -> str: ...

    @property
    def configuration_versions(self) -> frozenset[str]: ...

    @property
    def provider_configuration_model(self) -> type[BaseModel]: ...

    @property
    def credential_model(self) -> type[BaseModel] | None: ...

    def validate_configuration(
        self,
        *,
        schema_version: str,
        value: JsonValue,
    ) -> BaseModel: ...

    @property
    def supports_stop(self) -> bool: ...

    @property
    def supports_destroy(self) -> bool: ...

    @property
    def requires_keepalive(self) -> bool: ...

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

A small capability declaration describes supported stop/destruction and whether keepalive is required. A Host rejects unsupported policies before target I/O. The same implementation supplies preparation, resume, connections and target lifecycle operations through its Environment objects; there are no separately registered attachment or retention Providers.

There is no separate Provider factory entity. Catalog loading creates an `EnvironmentProvider` directly through the trusted entry point. There is no lifecycle Provider, Resource, attachment, or binding layer between Provider and Environment.

## Catalog and Discovery

The package owns metadata-only discovery values, validated process-local registrations, and one immutable selected catalog:

```python
@dataclass(frozen=True, slots=True)
class EnvironmentProviderReference:
    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderRegistration:
    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None
    builtin: bool


class EnvironmentProviderCatalog(Mapping[str, EnvironmentProvider]):
    @property
    def registrations(self) -> tuple[EnvironmentProviderRegistration, ...]: ...

    def require(self, provider_key: str) -> EnvironmentProvider: ...


def discover_environment_provider_references() -> tuple[EnvironmentProviderReference, ...]: ...


def build_environment_provider_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_providers: Iterable[EnvironmentProvider] = (),
) -> EnvironmentProviderCatalog: ...
```

`discover_environment_provider_references()` reads installed entry-point metadata and returns references sorted by Provider key, distribution name, distribution version, and import target. It does not import entry-point targets. References describe availability only and are not catalog registrations or authorization decisions.

`build_environment_provider_catalog()` creates one caller-owned immutable snapshot. It validates all requested keys, rejects duplicates and collisions among built-in, extension, and explicit sources, and verifies that every selected extension has exactly one installed entry point before importing any selected extension target. It then registers built-ins in `builtin_keys` order, installed extensions in `extension_keys` order, and explicit Provider objects in supplied order. An empty selection performs no entry-point scan.

The built-in catalog keys are:

| Key                 | Target                                                            |
| ------------------- | ----------------------------------------------------------------- |
| `a13n.direct-local` | One Host-selected local root using direct operating-system access |
| `a13n.local-envd`   | One Host-selected workspace served by a fresh local envd process  |
| `a13n.docker`       | One Docker container running envd                                 |
| `a13n.e2b`          | One native E2B sandbox                                            |

Third-party Providers register under the `a13n_environment_provider.providers` entry-point group. One selected entry point must load one concrete `EnvironmentProvider` class with safe no-argument construction. Preconstructed objects are not valid entry-point targets. The entry-point name and constructed `provider.key` must match. Only selected extension keys are imported.

Explicit Provider objects support embedded applications, tests, and source-level development without installed distribution metadata. They enter the same immutable catalog and Provider validation path. Their registrations have no import target or distribution provenance and are not built-ins.

The catalog is a `Mapping`: ordinary indexing has standard `KeyError` behavior, while `require()` validates the key and raises a stable Provider catalog error when the Provider was not selected. The catalog exposes no mutation, late loading, process-global registry, arbitrary serialized import target, ambient activation, or module replacement. Changed Provider code requires a fresh Host process.

Duplicate keys, malformed keys, missing entry points, metadata failures, import failures, wrong target types, mismatched keys, and Provider-construction failures are bounded `EnvironmentProviderError` values. Catalog errors use `provider_catalog_key_invalid`, `provider_catalog_duplicate`, `provider_catalog_missing`, `provider_catalog_load_failed`, or `provider_catalog_target_invalid`; they suppress native exception text and expose only bounded Provider and distribution context. Catalog construction never returns a partial ambiguous selection.

## Selection and Authorization

Catalog presence does not authorize use. A Host:

1. selects an allowed key from trusted definition or deployment configuration;
2. resolves the Provider from an allowlisted catalog;
3. validates the exact configuration version and payload;
4. resolves current authoritative state and fresh runtime collaborators;
5. calls `create_environment()`;
6. selects eager preparation or supplies lazy preparation coordination, then passes the Environment to Harness; stop, keepalive and destroy remain Host-only actions.

Model content, imported Harness state, a package installed in the environment, or an arbitrary entry-point key cannot select a Provider or supply runtime collaborators.

A state value cannot retarget configuration. The Provider validates that its opaque state payload is compatible with the selected configuration. A mismatched Provider key, configuration fingerprint, target metadata, or immutable identity fails rather than being adopted or rewritten.

## Configuration Evolution

Configuration `schema_version` and `EnvironmentState.state_version` evolve independently.

- A new configuration version changes desired configuration syntax or semantics.
- A new state version changes the provider-owned re-entry payload codec.
- Supporting a new configuration version does not imply accepting old state versions.
- Supporting a state migration does not authorize changing desired configuration.
- Providers reject unsupported versions explicitly.
- Hosts migrate stored configuration or state only through an explicit provider-owned migration boundary. Preparation never silently rewrites incompatible data.

Configuration normalization produces a stable fingerprint when a provider needs to prove that state belongs to the selected desired configuration. The fingerprint is compatibility evidence, not authorization or target identity.

## Failure Semantics

| Failure                                       | Behavior                                                            |
| --------------------------------------------- | ------------------------------------------------------------------- |
| Unknown or disabled Provider key              | Fail before configuration validation or runtime resolution          |
| Duplicate catalog key                         | Fail catalog construction                                           |
| Unsupported configuration version             | Fail with supported versions and no provider effects                |
| Invalid configuration payload                 | Fail with bounded field diagnostics and no provider effects         |
| Mismatched state Provider key                 | Fail before Environment construction                                |
| Invalid state JSON or unsupported codec       | Fail during deterministic state validation                          |
| Missing runtime collaborator                  | Fail Environment construction or preparation before target mutation |
| Entry-point import or Provider creation fails | Fail catalog loading; do not silently omit an enabled Provider      |

Errors never expose credentials, bearer URLs, Docker socket details, raw Host paths outside safe configuration diagnostics, or native exception text to model-facing surfaces.

## Compatibility

Provider keys are stable serialized discriminators. A key changes only for a semantically distinct Provider family. Configuration and state version changes follow their explicit compatibility boundaries.

`EnvironmentProvider`, `Environment`, and `EnvironmentState` define the shared lifecycle API. Host resources and lifecycle policy do not become another shared Provider hierarchy. Installed code provenance is diagnostic metadata, not a per-Environment exact Python package lock.

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
