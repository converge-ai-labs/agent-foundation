# Extension and Capability Discovery

## Design Position

The dedicated `a13n_ui.extensions` feature namespace owns Agent UI discovery, catalog projection, configured-extension validation, and management commands. It exposes one local catalog surface for all Capabilities available to the Agent composer and for the three standard installed extension planes:

1. Harness middleware Plugin factories;
2. Environment Providers;
3. Environment Run Extension factories.

Discovery reports availability. File-defined configured resources provide desired behavior. A Thread selection grants use. These three layers remain separate, and package presence never enables code.

Capabilities are listed beside extensions for Agent composition but are not misrepresented as a fourth Harness plugin plane. The Harness owns their construction and lifecycle; Agent UI owns the Host-authorized catalog it makes selectable.

## Catalog Surface

A common detached projection supports WebUI, CLI, and diagnostics:

```python
class CatalogReference(BaseModel):
    kind: Literal[
        "capability",
        "harness_plugin",
        "environment_provider",
        "environment_run_extension",
    ]
    key: str
    source: Literal[
        "pydantic", "harness", "agent_ui", "installed", "host"
    ]
    distribution_name: str | None
    distribution_version: str | None
    import_target: str | None
    configurable: bool
```

The common projection does not create a common runtime interface. Each kind retains its owning catalog, configuration, instance identity, ordering, and lifecycle. `agent_ui` means a release-owned built-in; `host` means an explicit embedding registration without implied package metadata.

Catalog refresh is process-local and reads package metadata or explicit Host registrations. It does not write resource files, modify global defaults, or alter Thread selections. Duplicate keys and invalid metadata are explicit diagnostics rather than last-one-wins resolution.

## Capability Catalog

Agent UI enumerates the complete declarative Capability catalog available to its Agent resolver:

- native Pydantic AI declarative Capability types;
- the closed Harness first-party declarative set;
- Agent UI-owned selectable Capabilities;
- exact custom types admitted through the Agent UI Host capability catalog.

The resulting selected custom set is supplied to the Harness `CapabilityTypeCatalog`. YAML names a stable serialization name and JSON-compatible arguments; it never supplies a Python import target.

Installed custom Capability packages may contribute concrete declarative types through the Agent UI-owned `a13n_ui.capabilities` entry-point group. An entry-point name equals the type's serialization name and loads one concrete directly dataclass-declared `AbstractCapability` type. Metadata discovery does not import targets. Catalog construction imports only selected names and applies all Harness `CapabilityTypeCatalog` collision, reserved-type, and schema checks.

Reserved mandatory infrastructure and `RunBindings`-only authority are visible in diagnostics when useful but are not configurable Agent resources. Capability availability does not enable it; an Agent must select it explicitly. Toolsets remain owned by Capabilities rather than entering a parallel package-discovery system.

## Harness Plugin Resources

Harness Plugin packages use the upstream `a13n_harness.plugins` entry-point group and `discover_harness_plugin_factory_references()`. Agent UI does not duplicate that loader.

A configured resource has this serialized shape:

```yaml
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory
plugin_key: vendor.memory
configuration: {}
```

`id` is the stable configured instance identity supplied as `plugin_id`. `plugin_key` selects the installed factory. Several resources may use one key with different IDs and configurations.

At generation validation Agent UI builds a catalog containing only referenced keys and invokes package-owned configuration validation. At Run capture it records normalized configuration and exact registration provenance. At native Agent construction it creates a fresh Plugin instance for every resolved Agent node that selects the resource.

Plugin order is the selection order stored by the Agent or Thread. There is no resource-global `enabled` flag: membership in an Agent or Thread selection enables one configured instance.

## Environment Provider Discovery and Profile Resources

Environment Provider packages use `a13n_environment_provider.providers`, `discover_environment_provider_references()`, and `build_environment_provider_catalog()`. Agent UI consumes these public values directly and does not reimplement entry-point loading.

An Environment profile is an Agent UI resource that selects one installed Provider and one approved Host adapter configuration. It is not the Provider implementation and is distinct from the runtime `Environment.environment_id`. A configured profile has this conceptual serialized shape:

```yaml
schema_version: "1"
kind: environment_profile
id: environment-docker
name: Docker
provider_key: a13n.docker
provider_schema_version: "1"
provider_configuration: {}
adapter_key: a13n.docker-project-roots
adapter_configuration: {}
```

`provider_configuration` contains desired Provider behavior independent of a particular Project root. The Agent UI Host adapter validates that template, resolves runtime collaborators, and binds each captured Project root into one fresh Provider configuration and `Environment` adapter.

Provider discovery alone can prove installation but cannot prove that Agent UI knows how to map local Project roots or construct Docker, E2B, credential, transport, or bootstrap collaborators. A discovered Provider without an approved Host adapter is reported as installed but not configurable. Native, Local EIP, and other Agent UI-supported Providers use release-owned adapters. Explicit embedding integrations can add exact approved adapters without placing Python import targets in YAML.

A Thread selects exactly one Environment profile. Omission during new root Thread creation resolves through defaults and finally to the release-owned Native profile. A Run never silently falls back to Native after another selected profile or Provider fails.

## Environment Run Extension Resources

[Harness Environment Integration](../agent-harness/08-environment-integration.md#environment-run-extensions) owns the extension protocol, factory catalog, ordering, and cleanup. Environment Run Extension packages use `a13n_harness.environment_run_extensions` and that upstream metadata discovery and selected factory catalog.

```yaml
schema_version: "1"
kind: environment_run_extension
id: extension-root-marker
name: Root Marker
extension_key: vendor.root-marker
configuration: {}
```

`id` is supplied as `extension_id`. Selection order is lifecycle order: Harness enters extensions in selection order after the initial Environment aggregate is entered and exits them in reverse order before Provider teardown.

The upstream factory creates a fresh pre-entry-inert extension and validates the public return boundary. Agent UI may construct and discard an inert candidate while validating a configuration generation. It records canonical configuration and exact factory provenance in each Run composition and creates fresh instances for root and child Runs.

## Selection Ownership

| Kind                      | Configured by                  | Selected by                                 | Fresh runtime scope                            |
| ------------------------- | ------------------------------ | ------------------------------------------- | ---------------------------------------------- |
| Capability                | Agent capability specification | Agent                                       | Native Agent/Run according to Capability hooks |
| Harness Plugin            | Extension YAML                 | Agent default or root/child Thread override | Each resolved Agent definition                 |
| Environment profile       | Extension YAML                 | Thread                                      | Fresh Provider and adapter per Project root    |
| Environment Run Extension | Extension YAML                 | Thread                                      | Complete Environment aggregate for each Run    |

An Agent can select Capabilities, Harness Plugins, MCP servers, and tool visibility. A root Thread can replace the Agent's Harness Plugin and MCP defaults. A child Thread retains its selected Agent-resource or Markdown-subagent source and initializes Host selections under the rules in [Projects, Threads, and Environments](04-projects-threads-and-environments.md#sticky-thread-configuration), then owns its sticky selections.

## Run Dependency Provenance

A resolved Run composition records the available identity of each behavior-affecting implementation:

```python
class DependencyProvenance(BaseModel):
    kind: Literal[
        "capability",
        "harness_plugin",
        "environment_provider",
        "environment_run_extension",
        "environment_adapter",
    ]
    key: str
    source: Literal["installed", "host"]
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None
```

Installed entry points record import and distribution metadata when available. Explicit Host registrations record their stable registration key and concrete class identity and can omit package metadata. The two forms never fabricate missing fields.

This value explains what the admitted Run used. Model routes, authentication kinds, and built-in MCP transports are already captured in their normalized definitions and do not receive invented adapter keys. Dependency provenance is not a compatibility gate for the next Run, installed-package authorization, or an extra version contract. Current catalog resolution validates that the selected implementation exists before construction. Already imported code is never hot-replaced inside one App process.

## Failure Semantics

| Failure                                        | Outcome                                                                    |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| Metadata enumeration failure                   | Catalog refresh fails with bounded diagnostics                             |
| Duplicate installed key                        | The key is ambiguous and cannot be selected                                |
| Selected target import or construction failure | Generation validation or Run capture fails; no partial catalog is accepted |
| Invalid Capability type                        | It is rejected by Host and Harness catalog validation                      |
| Provider has no approved Agent UI adapter      | It remains discoverable but cannot be selected for execution               |
| Factory configuration invalid                  | The resource generation is rejected                                        |

## Invariants

1. Capability discovery and the three extension planes are visible through one management surface but retain distinct runtime contracts.
2. Metadata discovery does not import targets.
3. Only selected keys are imported and constructed.
4. YAML never contains a Python import target.
5. Availability, configured resource identity, and Thread/Agent selection are separate facts.
6. Toolsets are configured through their owning Capabilities rather than a parallel discovery plane.
7. Every independent Run receives fresh extension instances.
8. An Environment profile is a Host configuration resource, not a runtime `Environment` identity.
9. A Provider without a Host Project-root adapter is not executable merely because it is installed.
