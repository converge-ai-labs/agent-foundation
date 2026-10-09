# Provider Subsystem

## Design Position

A Provider is an inert immutable value that declares how to reach one external capability and how to open it. `a13n_harness.providers` owns Model, Web, Connector, and Memory definitions and Host-side selection infrastructure. Environment Provider definitions and implementations belong to the independent [Environment library](../a13n-environment/01-environment-contract.md). Harness catalogs and installed manifests consume those definitions through their declared metadata, without requiring Environment to import Harness.

Providers are values, not registries. Importing a definition performs no I/O, opens no client, and grants no authority. A Host selects the definitions its deployment trusts, supplies validated configuration and a current credential, and owns the resulting resource lifetime.

## Boundaries

| Concern                                                       | Owner                                            |
| ------------------------------------------------------------- | ------------------------------------------------ |
| Identity, typed input models, credential declaration          | `ProviderDefinition` in the owning domain module |
| Domain operation contract                                     | The domain's definition type                     |
| Native definitions shipped with Harness                       | Each domain's built-in tuple                     |
| Installed third-party Environment definitions                 | `ProviderManifest` under one entry-point group   |
| Selection and unique type per domain                          | `ProviderCatalog`                                |
| Credential storage, encryption, rotation, and current value   | Host                                             |
| Resource records, authorization, and deployment configuration | Host                                             |
| Schema projection to a user interface                         | Host, from the definition's declared models      |
| Live client lifetime                                          | The caller that opens the Provider               |

The subsystem owns no durable record, no process-global registry, no ambient credential discovery, and no dynamic import target. `a13n_harness` never imports a Host implementation, and metadata for every domain loads without importing an optional vendor SDK.

## Shared Core

Harness-owned domain definitions are frozen dataclasses extending the following core. Environment owns its independent definition contract; Host catalog and schema projection accept that contract without requiring inheritance from a Harness class:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderDefinition[C: BaseModel, K: BaseModel]:
    DOMAIN: ClassVar[str]

    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K] | None = None
    authentication: Authentication = Authentication()
    setup_url: str | None = None
    setup_label: str | None = None
```

- `type` is the stable serialized discriminator. It matches `^[a-z][a-z0-9_]{0,63}$`; the same pattern validates every persisted Provider type across domains, including `EnvironmentState.provider_key`. A type changes only for a semantically distinct Provider family.
- `display_name` is provider-owned presentation metadata, bounded to 128 characters. Hosts read it directly instead of keeping a parallel name table.
- `configuration_model` describes the non-secret inputs needed to reach the capability. `credential_model` is `None` for a Provider that accepts no credential at all; such a definition can only forbid credentials, so it declares no other `Authentication`.
- `setup_url` must be an HTTPS URL without embedded credentials; `setup_label` is meaningful only alongside it. Together they let a Host link an operator to the vendor's own console without a per-vendor branch.

Construction validates this metadata eagerly, because an installed definition is untyped input: the type pattern, display name, setup link, and both Pydantic schemas are checked, and each schema must describe a bounded self-contained object with no remote `$ref`. A definition that fails validation is rejected at import, not at first use. Each domain adds its own rules through `validate_domain()`.

`parse_credential()` enforces the declared presence rule and then validates the credential against `credential_model`. Secret values use secret types, stay out of representations, and are revealed only at the native SDK or wire boundary. Credentials may contain nested objects and non-string values; they are not serialized JSON hidden in a string field.

## Authentication

Harness-owned domains use the following credential-presence declaration. Host projections preserve the same required/optional/forbidden semantics for the independently defined Environment metadata:

```python
class Authentication(BaseModel):
    mode: CredentialMode = CredentialMode.required
    cases: tuple[AuthenticationCase, ...] = ()


class AuthenticationCase(BaseModel):
    field: str
    equals: str | int | bool | None
    mode: CredentialMode
```

`CredentialMode` is `required`, `optional`, or `forbidden`. A case names a declared configuration field and is resolved against the validated configuration, including defaults, with exact JSON scalar equality: boolean `true` is distinct from integer `1`. A matching case overrides the base mode. Duplicate conditions, conditions naming an undeclared field, and simultaneously matching conflicting modes are definition errors. Required mode rejects an absent credential, forbidden mode rejects a present one, and optional mode accepts either; a present credential always passes the declared typed schema.

Hosts project this declaration alongside the configuration and credential schemas, so one form implementation serves every Provider without switching on a vendor name. Persistence semantics stay with the Host: omission on a partial update retains the stored credential, replacement validates the complete object, and explicit removal is accepted only when the resulting configuration allows absence.

## Domain Additions

| Domain      | Definition type                 | Adds                                                                                                                       |
| ----------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Model       | `ModelProviderDefinition`       | Supported native calling APIs, endpoint derivation, reserved headers, native Provider construction, optional bounded probe |
| Web         | `WebProviderDefinition`         | Optional `search` and `scrape` operations and declared restricted-scrape support                                           |
| Connector   | `ConnectorProviderDefinition`   | Setup validation and a scoped provider runtime over one bounded HTTP client                                                |
| Memory      | `MemoryProviderDefinition`      | Opening a record store bound to one namespace, over the caller's HTTP client or a store-owned public HTTPS client          |
| Environment | `EnvironmentProviderDefinition` | Imported single-target management, fixed-target `EnvironmentConnector`, and `EnvironmentExecution` contracts               |

[Model Provider Definitions](16b-model-provider-definitions.md) owns Model construction and native API bindings; [Record Memory](21a-record-memory.md#memory-providers) owns the Memory domain; [Environment Providers](../a13n-environment/01-environment-contract.md) owns the Environment domain. Declared capability flags are the single source a Host reads before offering an action; a domain rejects a definition whose flags contradict its supplied operations.

A definition never stores durable state, chooses retention, or associates a Thread. Acquiring a live resource is a separate explicit call that returns a scoped object owned by the caller.

### Default HTTP Proxy Routing

The default Web, Connector and Memory HTTP clients honor standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` environment variables and lowercase forms through the HTTP library. Endpoint validation, redirect restrictions, response bounds and existing retry semantics remain in effect; enabling a proxy adds no mutation retries. `EndpointPolicy` validates URL syntax, credentials and HTTPS exceptions and accepts the caller's immutable [Run configuration](06-execution-context-and-lifecycle.md#run-configuration) for exact hostname authorization. It performs no destination DNS precheck, resolved-address classification or IP pinning. Direct and proxy routes use native transport routing; the deployment operator owns DNS and network policy, and a failed proxy never falls back to direct. Explicitly injected clients retain their owner's routing policy and must enforce the accepted configuration themselves. [Model transport](16-input-model-and-output.md#model-construction-and-resolution) owns the Model-specific retry and custom-transport behavior.

### Connector Tool Versions

A Connector runtime exposes `tool_catalog(connector_key, *, provider_version=None)`. An explicit version selects that exact upstream tool definition version for every directory page and sparse detail request. Invalid or unavailable versions and mismatched response identities fail without selecting another version. Omitting the argument discovers the current catalogue and holds its selected version across subsequent pages. Runtime implementations, including installed plugins, accept the keyword; this requirement also applies to implementations that support only one version.

Composio versions use the dated `YYYYMMDD_NN` format. Saved setup options may select a supported older version after current application metadata advances. Setup still validates the live application, enabled authentication configuration, and instance fields; an older version requires matching native tool definitions. Account enrollment does not silently change the saved tool version. Execution supplies its selected version explicitly.

### Connector Setup Identity

`SetupStarted.expected_metadata` is a required `dict[str, str]` of provider-resolved identity predicates captured when setup starts. A Provider explicitly returns `{}` when no additional metadata predicates apply. Hosts retain this snapshot with the pending setup and require every key to have the same value in the ready account's `ConnectionInspection.safe_metadata` before granting account authority. Missing or null inspection values do not satisfy a string predicate; extra display metadata is ignored. This check supplements the Provider's account, application, and user identity checks.

Composio captures the actual authentication configuration ID and scheme selected by setup, including the concrete configuration resolved from `create:SCHEME`. Account inspection exposes these bounded values in safe metadata and maps a disabled authentication configuration to disabled account status. Hosts compare against the captured values, not a later catalogue lookup or the unresolved setup selector. Account state, credentials, and provider parameters are not safe metadata.

## Catalogs

```python
class ProviderCatalog[D](Mapping[str, D]):
    def require(self, provider_type: str) -> D: ...
```

Catalog construction validates the selected domain's definition contract, including imported Environment definitions. A catalog is one immutable snapshot of the definitions a deployment selected for a single domain. It rejects a duplicate type at construction and exposes no mutation, late loading, ambient activation, or module replacement; changed Provider code requires a fresh process. Ordinary indexing has standard `Mapping` behavior, while `require()` raises `ProviderNotSelected` so a Host can map a stored-but-unselected type to a safe configuration error instead of an unhandled failure.

Catalog presence never authorizes use. A Host resolves an allowed type from trusted configuration, validates the exact inputs, resolves the current credential, and only then opens the Provider. Model content, imported Harness state, and a package installed in the environment cannot select a Provider or supply collaborators.

## Installed Plugins

A third-party distribution contributes Environment Providers through exactly one entry-point group, `a13n_harness.providers.plugins`, whose target is an immutable manifest value. This is Harness Host discovery; the Environment library itself requires neither this loader nor Harness to use a definition directly:

```python
@dataclass(frozen=True, slots=True)
class ProviderManifest:
    api_version: int
    environment: tuple[EnvironmentProviderDefinition, ...] = ()
```

`api_version` is a fixed literal declared by the author, compared with the supported version rather than derived from the installed Harness. `environment` must be an immutable tuple of `EnvironmentProviderDefinition` values. One distribution may publish several named entry points. Model, Web, Connector, and Memory definitions are composed by the Host in code.

`load_provider_plugins(enabled)` imports only the entry-point names the deployment selected. It rejects a duplicate or malformed selected name, a selected name that is not installed, an ambiguous name matching several installed entry points, and a target that is not a `ProviderManifest`. An empty selection performs no metadata scan and imports nothing. The loader reports the entry-point name, distribution name, distribution version, and import target as diagnostic provenance; provenance is not authorization.

Installation alone activates nothing: the deployment names entry points, never an import target. There is no second loader, no registration callback, no process-global registry, and no per-domain entry-point group. Host applications that also select Harness business plugins use the separate `a13n_harness.plugins` and `a13n_harness.environment_run_extensions` groups, which contribute execution behavior rather than Providers.

## Host Composition

A Host builds one `ProviderCatalog` per domain from its native definitions plus, for Environment, the selected manifests, so a plugin type and a native type collide loudly instead of shadowing each other. Harness UI selects installed manifests through `load_provider_plugins()` for its local extensions. a13n Service registers its definitions in code through `Distribution.providers` ([Assembly](../a13n-service/09-runtime.md#assembly)).

A Host projects safe metadata for each selected definition: `type`, `display_name`, the configuration and credential JSON Schemas, the `Authentication` declaration, `setup_url`, `setup_label`, and the domain's declared capabilities. The projection contains no credential value, no native client, and no import target.

## Failure Semantics

| Condition                                                       | Behavior                                                       |
| --------------------------------------------------------------- | -------------------------------------------------------------- |
| Invalid type, display name, setup link, or declared schema      | Fail at definition construction                                |
| Authentication condition naming an undeclared field             | Fail at definition construction                                |
| Duplicate type within one domain catalog                        | Fail catalog construction                                      |
| Selected plugin missing, ambiguous, or not a manifest           | Fail selection; never silently omit a selected Provider        |
| Unsupported manifest API version                                | Fail selection                                                 |
| Referenced type absent from the catalog                         | `ProviderNotSelected`, projected as a safe configuration error |
| Credential absent under `required` or present under `forbidden` | Fail before any external call                                  |
| Invalid configuration or credential payload                     | Fail with bounded field diagnostics and no external effects    |

Errors expose bounded Provider and distribution context. They never expose credentials, bearer URLs, or native exception text on a model-facing surface.

## Compatibility

Provider `type` values are stable serialized discriminators shared by configuration records, state envelopes, and host APIs. Adding a field to a definition is additive; adding a required declared Environment capability is a breaking change for installed definitions and advances the manifest API version. Installed code provenance is diagnostic metadata, not a per-resource Python package lock.

Definitions carry no configuration schema version. A Provider owns exactly one configuration model, one optional credential model, and, for Environment, one target recipe model; changing an input's meaning changes the Provider type rather than introducing a parallel versioned schema.

## Invariants

01. Harness-owned domains share one definition core; imported Environment definitions remain independently usable and retain the same Host-facing metadata semantics.
02. Defining and selecting a Provider performs no external I/O and creates no client.
03. `a13n_harness.providers.plugins` is the only Provider entry-point group and the only authoring surface for installed Environment definitions.
04. A deployment selects entry-point names; it never supplies an import target.
05. One `ProviderCatalog` per domain owns the unique-type rule and is immutable after construction.
06. Catalog membership never grants authority, and a missing type is a safe configuration error.
07. Credential presence has one meaning across definitions, hosts, and forms.
08. Credentials and live collaborators never enter configuration, portable state, or Harness continuation.
09. Importing definition metadata never imports an optional vendor SDK or a Host implementation.
10. A Provider definition owns no durable record, retention policy, or resource authority.
