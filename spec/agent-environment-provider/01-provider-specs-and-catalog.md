# Provider Specifications and Catalog

## Design Position

An `EnvironmentProviderSpec` is the serializable, credential-free description of one desired Environment provider resource. It identifies a trusted provider key, one provider-owned configuration schema version, and a finite JSON parameter object. The same selected factory validates that document wherever a Host accepts, stores, compares, or executes it.

The specification is not a Harness binding, live resource, provider launch record, arbitrary import instruction, or user authorization decision. Validation proves schema compatibility only. A Host separately decides which provider keys and parameter values are allowed for the current caller and deployment.

## Serialized Specification

The following schema is the stable serialized envelope:

```python
class EnvironmentProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str
    schema_version: str
    parameters: Mapping[str, JsonValue] = Field(default_factory=dict)
```

`provider_key` is a bounded namespaced identifier. `schema_version` selects the provider-owned parameter schema and migration rules. `parameters` is recursively detached, finite JSON. It contains desired resource properties only and never contains:

- credentials or credential references usable without current Host authorization;
- provider lifecycle identity such as a container or sandbox ID;
- an endpoint, socket, EIP session, or attachment credential;
- launch, lease, destroy, retry, or reconciliation state;
- a Python import target, object, callable, or serialized Harness value.

A Host can wrap this envelope in its own named resource, policy, revision, or persistence schema. Those outer records are not part of the provider package contract.

## Factory Contract

The conceptual factory API is process-local:

```python
ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP = (
    "a13n_environment_provider.providers"
)


class EnvironmentProviderRuntime(ABC):
    """Provider-owned process-local runtime collaborator marker."""


class EnvironmentProviderFactory(ABC):
    @classmethod
    def provider_key(cls) -> str: ...

    @classmethod
    def supported_schema_versions(cls) -> frozenset[str]: ...

    @classmethod
    def configuration_model(
        cls,
        schema_version: str,
    ) -> type[BaseModel]: ...

    @abstractmethod
    def lifecycle_capabilities(
        self,
        configuration: BaseModel,
    ) -> EnvironmentLifecycleCapabilities: ...

    @abstractmethod
    def create_provider(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider: ...
```

Each factory owns exactly one provider key and one or more explicitly supported configuration schema versions. `configuration_model()` returns the exact frozen Pydantic model for a supported version. The catalog validates `parameters` with `extra="forbid"` behavior, preserves no caller-owned mutable collection, and produces a process-local resolved value:

```python
@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentProviderSpec:
    spec: EnvironmentProviderSpec
    configuration: BaseModel
    lifecycle_capabilities: EnvironmentLifecycleCapabilities
    factory: EnvironmentProviderFactory
```

The resolved value is not serialized. It retains trusted code selected by the Host and can construct an `EnvironmentProvider` from one fresh process-local `EnvironmentProviderRuntime`. `lifecycle_capabilities()` is a pure, synchronous inspection of the already validated configuration. It performs no provider construction or external I/O and returns the exact allocation, attachment-concurrency, and pause capabilities that a provider created from that configuration will expose. A Host can therefore validate lifecycle and topology compatibility before acquiring runtime authority or causing provider effects. Catalog construction verifies that the created Provider reports exactly the introspected capabilities and rejects a mismatch.

The runtime abstract marker has no Host or Harness dependency; each provider owns one exact typed runtime implementation carrying only current credential/client factories and other live collaborators. Runtime values are non-serializable and never enter provider specifications or resource state. Provider construction is synchronous and inert: it captures the runtime but performs no filesystem, Docker, provider, credential, network, daemon, or package-discovery I/O and creates no cleanup obligation.

## Built-in and Extension Catalog

The package ships four factories under exact keys:

- `a13n.direct-local`;
- `a13n.local-envd`;
- `a13n.docker`;
- `a13n.e2b`.

Built-ins are selectable directly and do not depend on installed entry-point metadata. Third-party distributions can register one factory class:

```toml
[project.entry-points."a13n_environment_provider.providers"]
"acme.sandbox" = "acme_environment.provider:AcmeEnvironmentProviderFactory"
```

Metadata discovery returns bounded references without importing targets. Catalog construction accepts explicit built-in keys, selected third-party keys, and direct factory instances. It preflights missing keys and every collision before importing a selected target. A third-party entry cannot replace or alias a built-in key. Empty third-party selection scans and imports no distribution metadata.

```python
@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryReference:
    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryRegistration:
    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None


class EnvironmentProviderFactoryCatalog(
    Mapping[str, EnvironmentProviderFactory]
):
    @property
    def registrations(
        self,
    ) -> tuple[EnvironmentProviderFactoryRegistration, ...]: ...

    def require(
        self,
        provider_key: str,
    ) -> EnvironmentProviderFactory: ...

    def resolve_spec(
        self,
        spec: EnvironmentProviderSpec,
    ) -> ResolvedEnvironmentProviderSpec: ...

    def create_provider(
        self,
        spec: EnvironmentProviderSpec,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider: ...


def discover_environment_provider_factory_references(
) -> tuple[EnvironmentProviderFactoryReference, ...]: ...


def build_environment_provider_factory_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_factories: Iterable[EnvironmentProviderFactory] = (),
) -> EnvironmentProviderFactoryCatalog: ...
```

Built-in selection is explicit but requires no metadata scan or import target. Calling the discovery function explicitly scans installed entry-point metadata and returns deterministic bounded references without loading target code. Catalog construction loads only the requested extension keys, and an empty `extension_keys` value performs no metadata scan. Direct factory instances are already trusted process-local inputs; their keys still participate in complete collision checks. The returned catalog and registration sequence are immutable snapshots.

A selected entry point must resolve to an `EnvironmentProviderFactory` subclass with a safe no-argument constructor. The class's `provider_key()` must equal the entry-point name. Catalog construction instantiates each selected factory once and records bounded distribution provenance for Host lock verification. There is no mutable global registry or import-time auto-registration.

## Selection and Authorization

Installed, discoverable, selected, schema-valid, deployment-trusted, and caller-authorized are separate states. The package enforces selected-code and schema boundaries; the Host owns policy.

An API value, model value, `HarnessState`, provider parameter, resource-state payload, or database field cannot name an arbitrary `module:object`. A Host selects an exact provider key from its trusted catalog before Provider construction. Provider configuration cannot enable another provider, load an extension, or add a Harness Capability.

Provider schemas can perform deterministic semantic validation that is intrinsic to the provider, such as positive resource sizes or mutually exclusive Docker image selectors. Validation that requires credentials, current vendor state, filesystem inspection, network calls, or allocation belongs to an explicit async Provider operation rather than document decoding.

## Configuration Evolution

A provider configuration schema version changes independently from the package version, EIP version, Harness state version, and Host record version. A factory accepts only its declared versions. It does not infer a version from parameter shape or silently reinterpret an unknown version as latest.

A provider-owned migration is an explicit pure transformation from one accepted serialized `EnvironmentProviderSpec` to another. The caller decides whether to persist or use the migrated value. Migration performs no provider I/O and cannot produce resource state, current credentials, or runtime attachment authority.

Compatible changes within a schema version can only narrow implementation internals without changing accepted parameter meaning or defaults. Adding a new optional parameter with a stable default requires the selected model and canonical representation to agree across Host components. Removing, renaming, changing authority, or changing the meaning/default of a field requires another schema version.

## Failure Semantics

| Failure                                                         | Outcome                                                    |
| --------------------------------------------------------------- | ---------------------------------------------------------- |
| Invalid provider key or envelope                                | Bounded specification error; no target import              |
| Missing selected key                                            | Catalog error before Provider construction                 |
| Built-in or entry-point collision                               | Complete catalog construction fails                        |
| Import or factory construction fails                            | Bounded load error with protected cause                    |
| Unsupported schema version                                      | Explicit compatibility error; no fallback                  |
| Invalid or oversized parameters                                 | Validation error without Provider construction             |
| Capability inspection fails or returns an invalid value         | Bounded factory error before Provider construction         |
| Provider constructor raises                                     | Bounded factory error; no external cleanup may be required |
| Provider reports capabilities different from factory inspection | Factory target error; Provider is not returned             |
| Host policy denies a valid spec                                 | Host denial; package does not substitute another provider  |

Safe errors can include the bounded provider key, schema version, and distribution provenance. They never include parameter values, object representations, credentials, raw vendor exception text, or private installation paths.

## Compatibility

Provider key, configuration schema version, factory API, package version, vendor SDK range, EIP version, and Host persistence schema are independent compatibility axes. The Harness release group pins one provider-package version, but a serialized provider specification remains governed by its own `provider_key` and `schema_version`.

Changing an existing built-in key, making discovery implicit, allowing arbitrary import targets, making factory or Provider construction effectful, or treating schema validation as authorization is incompatible.

## Invariants

01. One serialized provider specification contains only a provider key, schema version, and finite credential-free desired parameters.
02. The selected provider factory is the sole owner of its parameter schema and migration meaning.
03. Built-in keys are exact, namespaced, and cannot be shadowed by entry points.
04. Package metadata is availability, not authorization.
05. Empty extension selection performs no metadata scan or target import.
06. Factory and Provider construction are deterministic, synchronous, and inert; a fresh typed runtime supplies current live collaborators without ambient credential lookup.
07. Provider configuration never carries live authority, resource state, attachment state, or a Python import target.
08. Unknown schema versions fail explicitly rather than receiving latest-version defaults.
09. Factory lifecycle-capability inspection is pure and configuration-aware; a constructed Provider must report the same immutable capability value.
10. Host policy can always deny or narrow a schema-valid provider specification.
