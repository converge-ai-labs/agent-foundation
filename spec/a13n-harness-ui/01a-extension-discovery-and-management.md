# Extension and Capability Discovery

## Design Position

The dedicated `a13n_harness_ui.extensions` feature namespace owns Harness UI discovery, catalog projection, configured-extension validation, and management commands. It exposes one local catalog surface for all Capabilities available to the Agent composer and for the three standard installed extension planes:

1. Harness middleware Plugin factories;
2. Environment Providers;
3. Environment Run Extension factories.

Discovery reports availability. File-defined configured resources provide desired behavior. A Thread selection grants use. These three layers remain separate, and package presence never enables code.

Capabilities are listed beside extensions for Agent composition but are not misrepresented as a fourth Harness plugin plane. The Harness owns their construction and lifecycle; Harness UI owns the Host-authorized catalog it makes selectable.

## Catalog Surface

A common detached projection supports CLI, embedding adapters, and diagnostics:

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
        "pydantic", "harness", "harness_ui", "installed", "host"
    ]
    distribution_name: str | None
    distribution_version: str | None
    import_target: str | None
    configurable: bool
```

The common projection does not create a common runtime interface. Each kind retains its owning catalog, configuration, instance identity, ordering, and lifecycle. `harness_ui` means a release-owned built-in; `host` means an explicit embedding registration without implied package metadata.

Catalog refresh is process-local and reads package metadata or explicit Host registrations. It does not write resource files, modify global defaults, or alter Thread selections. Duplicate keys and invalid metadata are explicit diagnostics rather than last-one-wins resolution.

## Capability Catalog

Harness UI enumerates the declarative Capability authoring catalog. `ToolPermissionsCapability` is omitted from user-facing catalog choices; ordinary configuration uses the root `security.shell_review` shortcut instead. Advanced Agent selections may configure this single Capability directly, including nested `review` configuration. Hiding authoring choices does not reject or remove explicit Agent policy.

The remaining catalog includes:

- native Pydantic AI declarative Capability types;
- the closed Harness first-party declarative set;
- Harness UI-owned selectable Capabilities;
- exact custom types admitted through the Harness UI Host capability catalog.

The resulting selected custom set is supplied to the Harness `CapabilityTypeCatalog`. YAML names a stable serialization name and JSON-compatible arguments; it never supplies a Python import target. Generic Capability construction passes configuration directly to the selected native constructor, without Host-added `validate_call` wrapping, argument filtering, scalar coercion, or validation aliases. Constructor keyword names and any `**kwargs` behavior remain native; unexpected keywords fail through normal Python binding instead of being silently discarded. Nested provider-specific Model settings remain intact. A type's own configuration model and constructor validation remain authoritative, including their default strictness. Harness UI performs only the serialization adaptation needed to instantiate the selected native value, such as enum values and JSON lists.

An unavailable, ambiguous, unloadable, or configuration-invalid Agent Capability is skipped individually with a warning identifying the Agent ID, Capability key, and safe reason. It does not reject the complete configuration generation or block other Agents. Native constructor validation still determines whether configuration is usable; warnings do not expose argument values or raw exception traces. Authored resource files remain unchanged. Configuration validation and App status expose the current process's warnings, and the interactive CLI displays them. Malformed resource structure and invalid non-Capability resources remain errors.

Only effective Capability selections enter a newly captured Run composition and its dependency provenance. Skipping an explicitly configured default Capability does not reintroduce it with default settings. Global tool switches and mandatory Host/runtime authority remain enforced. This tolerance applies to source selection, not reconstruction of immutable captures, runtime hook failures, unsupported native model requests, or incompatible stored Capability state; those failures retain their existing semantics.

`NativeTool` configuration uses the upstream `from_spec` reconstruction so both a flat `{kind: web_search, ...}` mapping and an explicit `{tool: {kind: web_search, ...}}` mapping produce typed native tools, not dictionaries. An Agent and its captured composition may contain multiple `NativeTool` entries to compose different tools. Other Capability keys retain their unique-selection rule; native tool merging and support remain upstream semantics.

The selectable `native_image_generation` key constructs Harness `NativeImageGenerationCapability`, validating its configuration as native `ImageGenerationTool` options and injecting Harness UI's Thread-file saver. The saver is never authored or persisted as a configuration value. This is native generation with saving, not Pydantic AI's separate image API/fallback Capability. [Native Image Generation](../a13n-harness/16-input-model-and-output.md#native-image-generation) owns its result semantics; [Thread file mounts](04-projects-threads-and-environments.md#thread-file-mount) owns the UI destination.

Installed custom Capability packages may contribute concrete declarative types through the Harness UI-owned `a13n_harness_ui.capabilities` entry-point group. An entry-point name equals the type's serialization name and loads one concrete directly dataclass-declared `AbstractCapability` type. Metadata discovery does not import targets. Catalog construction imports only selected names and applies all Harness `CapabilityTypeCatalog` collision, reserved-type, and schema checks.

Reserved mandatory infrastructure and `RunBindings`-only authority are visible in diagnostics when useful but are not configurable Agent resources. Capability availability does not enable it; an Agent must select it explicitly. Toolsets remain owned by Capabilities rather than entering a parallel package-discovery system.

## Harness Plugin Resources

Harness Plugin packages use the upstream `a13n_harness.plugins` entry-point group and `discover_harness_plugin_factory_references()`. Harness UI does not duplicate that loader.

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

At generation validation Harness UI builds a catalog containing only referenced keys and invokes package-owned configuration validation. At Run capture it records normalized configuration and exact registration provenance. At native Agent construction it creates a fresh Plugin instance for every resolved Agent node that selects the resource.

Plugin order is the selection order stored by the Agent or Thread. There is no resource-global `enabled` flag: membership in an Agent or Thread selection enables one configured instance.

## Environment Provider Discovery and Profile Resources

Environment extension packages use `a13n_environment.providers`, `discover_environment_provider_references()`, and `build_environment_provider_catalog()`. Harness UI consumes these public values directly and does not reimplement entry-point loading.

An Environment profile selects one installed Provider and one approved Host adapter configuration. It is not the Provider implementation and is distinct from the runtime `Environment.environment_id`.

Harness UI owns two fixed profiles that require no YAML resource:

| Stable profile ID     | Surface mode     | Provider          | Host adapter                   | Command authority                                                                               |
| --------------------- | ---------------- | ----------------- | ------------------------------ | ----------------------------------------------------------------------------------------------- |
| `environment-native`  | **Full Control** | `direct-local`    | `a13n.native-project-root`     | Direct Host-user execution with ambient Host filesystem and network access                      |
| `environment-sandbox` | **Sandbox**      | `a13n.local-envd` | `a13n.local-envd-project-root` | EIP execution in a Host-launched outer sandbox with denied networking; no Full Control fallback |

These IDs are release-owned and a configured resource cannot redefine them. `environment-native` remains the omission fallback for compatible existing configuration, while surfaces present it as **Full Control** rather than exposing “Native” as the user-facing safety label.

An advanced configured profile has this conceptual serialized shape:

```yaml
schema_version: "1"
kind: environment_profile
id: environment-docker
name: Docker
provider_key: docker
provider_schema_version: "1"
provider_configuration: {}
adapter_key: a13n.docker-project-roots
adapter_configuration: {}
```

`provider_configuration` contains desired Provider behavior independent of a particular Project root. The Harness UI Host adapter validates that template, resolves runtime collaborators, and binds each captured Project root into one fresh Provider configuration and `Environment` adapter. The adapter also declares whether its aggregate path presentation preserves canonical Host paths. An adapter that does not explicitly preserve them receives the provider-neutral virtual layout.

Provider discovery alone can prove installation but cannot prove that Harness UI knows how to map local Project roots or construct Docker, E2B, credential, transport, or bootstrap collaborators. A discovered Provider without an approved Host adapter is reported as installed but not configurable. Full Control, Sandbox, and other Harness UI-supported Providers use release-owned adapters. Explicit embedding integrations can add exact approved adapters without placing Python import targets in YAML.

A Thread selects one profile for local roots and may also select [Device Environment bindings](04a-devices-and-environment-bindings.md). Omission of the local profile during creation resolves through defaults and then Full Control. A Run never silently falls back after a selected Device, profile, Provider or Host sandbox fails.

## Environment Run Extension Resources

[Harness Environment Integration](../a13n-harness/08-environment-integration.md#environment-run-extensions) owns the extension protocol, factory catalog, ordering, and cleanup. Environment Run Extension packages use `a13n_harness.environment_run_extensions` and that upstream metadata discovery and selected factory catalog.

```yaml
schema_version: "1"
kind: environment_run_extension
id: extension-root-marker
name: Root Marker
extension_key: vendor.root-marker
configuration: {}
```

`id` is supplied as `extension_id`. Selection order is lifecycle order: Harness enters extensions in selection order after the initial Environment aggregate is entered and exits them in reverse order before Provider teardown.

The upstream factory creates a fresh pre-entry-inert extension and validates the public return boundary. Harness UI may construct and discard an inert candidate while validating a configuration generation. It records canonical configuration and exact factory provenance in each Run composition and creates fresh instances for root and child Runs.

## Selection Ownership

| Kind                      | Configured by                        | Selected by                                 | Fresh runtime scope                            |
| ------------------------- | ------------------------------------ | ------------------------------------------- | ---------------------------------------------- |
| Capability                | Agent capability specification       | Agent                                       | Native Agent/Run according to Capability hooks |
| Harness Plugin            | Extension YAML                       | Agent default or root/child Thread override | Each resolved Agent definition                 |
| Environment profile       | Harness UI release or Extension YAML | Thread                                      | Fresh Provider and adapter per Project root    |
| Environment Run Extension | Extension YAML                       | Thread                                      | Complete Environment aggregate for each Run    |

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

| Failure                                                       | Outcome                                                                              |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Metadata enumeration failure                                  | Catalog refresh fails with bounded diagnostics                                       |
| Duplicate installed key                                       | The key is ambiguous and cannot be selected                                          |
| Selected non-Capability target import or construction failure | Generation validation or Run capture fails; no partial extension catalog is accepted |
| Unavailable or invalid Agent Capability selection             | The entry is skipped with a warning; other valid entries remain selected             |
| Provider has no approved Harness UI adapter                   | It remains discoverable but cannot be selected for execution                         |
| Factory configuration invalid                                 | The resource generation is rejected                                                  |
| Captured Capability reconstruction or runtime failure         | The failure is propagated; immutable captures are not weakened                       |

## Invariants

01. Capability discovery and the three extension planes are visible through one management surface but retain distinct runtime contracts.
02. Metadata discovery does not import targets.
03. Only selected keys are imported and constructed.
04. YAML never contains a Python import target.
05. Availability, configured resource identity, and Thread/Agent selection are separate facts.
06. Toolsets are configured through their owning Capabilities rather than a parallel discovery plane.
07. Every independent Run receives fresh extension instances.
08. An Environment profile is Host configuration, not a runtime `Environment` identity.
09. Full Control and Sandbox are fixed release-owned profiles whose IDs cannot be shadowed by configuration resources.
10. A Provider without a Host Project-root adapter is not executable merely because it is installed.
