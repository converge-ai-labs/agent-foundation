# Integration Package Examples

This standalone project demonstrates the supported configuration and direct-code composition modes for the Agent Harness package-extension boundaries. Every path is runnable offline and covered by focused tests.

## Composition Matrix

| Boundary                  | Declarative or installed-package mode                                                                                                              | Explicit code mode                                                                                            |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Custom Capability         | Authorize an exact type with `CapabilityTypeCatalog`, then select its serialization name in `AgentSpec.capabilities`                               | Construct the Capability and place it in `AgentDefinition.capabilities`                                       |
| Environment provider      | Explicitly enable one installed `ProviderManifest` from `a13n_harness.providers.plugins`, then validate configuration and construct fresh adapters | Select an `EnvironmentProviderDefinition` object directly, then use the same validation and construction path |
| Environment run extension | Select an `EnvironmentRunExtensionFactory` from `a13n_harness.environment_run_extensions`, then call `create_extension()`                          | Supply an `EnvironmentRunExtensionFactory` object directly, then call the same `create_extension()` method    |
| Harness middleware        | Let a `HarnessBuildContext` load preferred YAML or JSON, select enabled `HarnessPluginFactory` entries, and apply fresh instances during build     | Construct an `AbstractHarnessPlugin` directly and place it in `AgentDefinition.plugins`                       |

Entry-point metadata provides only a stable key and import target. Harness middleware configuration uses the Harness-owned versioned envelope; YAML is preferred for files, JSON is supported for files and inline environment values, and each plugin package owns only the typed `configuration` payload.

All entry-point paths are explicit and lazy:

1. metadata discovery does not import target modules;
2. Environment callers select exact provider or run-extension keys, while the Harness builder selects only enabled document keys;
3. catalog construction imports only those selected targets;
4. factory output enters the same ordinary concrete-object path used by code mode.

Package presence is availability, not authorization. No catalog accepts an arbitrary import path or mutates a process-global registry.

## Quick Start

From the repository root:

```bash
make examples-check-all
```

From this directory:

```bash
uv sync --locked
uv run plugin-example-capability-agent-spec
uv run plugin-example-capability-code
uv run plugin-example-environment-entrypoint
uv run plugin-example-environment-code
uv run plugin-example-environment-extension-entrypoint
uv run plugin-example-environment-extension-code
uv run plugin-example-harness-entrypoint
uv run plugin-example-harness-code
uv run pytest
```

The project is intentionally outside the root release workspace. Its independent lock resolves the local checkout through:

```toml
[tool.uv.sources]
a13n-harness = { path = "../../packages/a13n-harness", editable = true }
```

A standalone integration distribution should remove those development sources and declare the released Provider and Harness ranges it supports.

## Environment Provider

The distribution exports one immutable manifest through the single Provider entry-point group:

```toml
[project.entry-points."a13n_harness.providers.plugins"]
workspace = "a13n_plugin_examples.environment:manifest"
```

[`environment.py`](src/a13n_plugin_examples/environment.py) contains:

- a strict package-owned schema-version-1 `WorkspaceEnvironmentConfiguration` model;
- a constant immutable `WORKSPACE_ENVIRONMENT` definition exported through `ProviderManifest`;
- a fresh `WorkspaceEnvironment` adapter backed by Direct Local operations.

Manifest import, configuration validation, and `construct()` perform no filesystem I/O. The workspace is checked when the Host explicitly prepares the adapter or its first operation requests readiness; scope entry performs no target I/O. The Provider is stateless because its target is the deterministic Host-selected directory; `dump_state()` returns `None`, and neither `close()` nor explicit `destroy()` deletes that directory.

### Installed entry-point mode

[`run_environment_entrypoint_demo()`](src/a13n_plugin_examples/demo_environment.py) explicitly enables only the `workspace` plugin, resolves its Environment definition, validates two configurations, and constructs two fresh Environments.

```bash
uv run plugin-example-environment-entrypoint
```

### Explicit code mode

[`run_environment_code_demo()`](src/a13n_plugin_examples/demo_environment.py) selects `WORKSPACE_ENVIRONMENT` directly without scanning package metadata. It then uses the same immutable catalog, validation, construction, and Harness Run path.

```bash
uv run plugin-example-environment-code
```

Both paths pass two already constructed adapters to a real offline Harness Run, verify default and qualified routing, export no state for the stateless mounts, close both adapters non-destructively, and preserve both Host directories:

```text
selection mode: entrypoint
selected provider: example_workspace
active aliases: source, docs
default route: source workspace
docs route: documentation workspace
exported state aliases: none
roots preserved: True
```

Code mode prints the same result with `selection mode: code`.

### Real Provider checklist

- Use one stable entry-point name and one stable Provider `type`.
- Keep definition construction, recipe validation, and Environment construction strict, bounded, and side-effect free.
- Declare one account model, one optional credential model, and one target recipe model; support exact state versions without fallback or shape inference.
- Acquire current credentials, SDK clients, bootstrap stores, and transport factories only inside `runtime_factory`.
- Construct one fresh Environment per independent Run or explicit Host lifecycle operation.
- Validate supplied state before target mutation; create a replacement only after authoritative absence.
- Update cached state as soon as changed target identity is known, before later readiness can fail.
- Keep `close()` process-local and non-destructive; expose backing-target removal only through explicit `destroy()` on a fresh adapter.
- Keep mount names, access ceilings, desired configuration, state authority, retention, authorization, and scheduling under Host control.

The example subclasses the public Direct Local adapter to stay focused on Provider packaging. A remote sandbox Provider can expose the same Provider-neutral operation surface through EIP without creating another Harness lifecycle layer.

## Environment Run Extension

The distribution registers a separate aggregate-lifecycle factory:

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"example.workspace-marker" = "a13n_plugin_examples.environment_extension:WorkspaceMarkerExtensionFactory"
```

[`environment_extension.py`](src/a13n_plugin_examples/environment_extension.py) contains:

- strict package-owned `WorkspaceMarkerConfiguration` validation;
- a no-argument, side-effect-free `WorkspaceMarkerExtensionFactory`;
- `WorkspaceMarkerExtension`, which creates a provider-neutral workspace marker after Environment state restoration and removes it before provider teardown.

The factory receives an `EnvironmentRunExtensionFactoryContext` with separate `extension_key`, `extension_id`, and detached JSON configuration. It returns a fresh pre-entry-inert extension. All file I/O occurs only inside `EnvironmentRunExtension.bind()`.

The demo passes the Provider's Environment through `EnvironmentMount` with an exact text-read, text-write, and remove ceiling. The same mount input works with `run()` and `create_environment_runtime()`; Harness owns single-use transfer, entry, and cleanup without an example-specific lifecycle adapter.

### Installed entry-point mode

[`run_environment_extension_entrypoint_demo()`](src/a13n_plugin_examples/demo_environment_extension.py) discovers metadata, selects only `example.workspace-marker`, creates one configured instance, registers it on `create_environment_runtime()`, and gives that runtime to a real offline `ExecutableAgent` through `RunBindings`:

```bash
uv run plugin-example-environment-extension-entrypoint
```

### Explicit code mode

[`run_environment_extension_code_demo()`](src/a13n_plugin_examples/demo_environment_extension.py) supplies `WorkspaceMarkerExtensionFactory()` directly without scanning installed metadata, then calls the same catalog method:

```bash
uv run plugin-example-environment-extension-code
```

Both paths verify through an input factory that the marker is available after Harness-managed aggregate activation and absent after the public Agent run closes:

```text
selection mode: entrypoint
selected extension: example.workspace-marker
extension id: marker-entrypoint
marker content: entrypoint:run-...
marker removed: True
```

Code mode reports `selection mode: code` and `extension id: marker-code`.

A run extension spans the complete `EnvironmentRuntime`, not one Provider adapter. It enters once in registration order after state restore, remains entered across mount mutations, and exits in reverse order while provider-neutral Environment operations are still available. It receives no model, `AgentContext`, Harness plugin context, or runtime mutation authority.

### Real run-extension checklist

- Give every aggregate instance a unique, stable, non-blank `extension_id`.
- Keep factory construction and `create_extension()` side-effect free.
- Validate the package-owned JSON configuration with a strict schema.
- Acquire all cleanup-producing resources inside `bind()`.
- Use only provider-neutral `Environment` operations when touching Environment resources.
- Make exit finite and clean every owned resource even when the run failed.
- Do not expect runtime mount mutations to rebind the extension.
- Use a Harness plugin for input/result middleware and a Capability for Agent-loop behavior instead.

## Custom Capability

Pydantic AI Capability is the extension point for instructions, Toolsets, request hooks, Agent-loop state, and native Agent/run lifecycle. Capability packages do not use a Harness entry-point group: a trusted Host imports and authorizes exact types, and `AgentSpec` selects only from that closed catalog.

[`capability.py`](src/a13n_plugin_examples/capability.py) defines `ExampleInstructionsCapability`, a directly declared dataclass with one stable serialization name and a package-owned instruction field.

### AgentSpec mode

[`run_capability_demo(selection_mode="agent-spec")`](src/a13n_plugin_examples/demo_capability.py) constructs an immutable `CapabilityTypeCatalog` from the exact custom type, then selects and configures it in `AgentSpec.capabilities`:

```python
catalog = CapabilityTypeCatalog.from_types(
    (ExampleInstructionsCapability,),
)
agent_spec = AgentSpec(
    capabilities=[
        CapabilitySpec(
            name="example_instructions",
            arguments={
                "instructions": "Selection mode is agent-spec.",
            },
        )
    ]
)
executable = HarnessBuilder(
    capability_type_catalog=catalog,
).build(
    agent_spec,
    output_type=str,
    model=model,
)
```

Run the complete offline path with:

```bash
uv run plugin-example-capability-agent-spec
```

The catalog makes one type available to that builder; it neither scans packages nor enables an instance. The `AgentSpec` entry is the separate selection step. An unregistered serialization name fails during Agent construction.

### Explicit code mode

Trusted embedded code can skip declarative reconstruction and supply the same concrete object directly:

```python
executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,
    capabilities=(
        ExampleInstructionsCapability(
            instructions="Selection mode is code.",
        ),
    ),
)
```

Run it with:

```bash
uv run plugin-example-capability-code
```

Both modes prove that the selected instruction reaches the offline Model and produce:

```text
selection mode: agent-spec
selected capability: example_instructions
capability instructions: Selection mode is agent-spec.
model received instructions: Selection mode is agent-spec.
output: offline capability response
```

### Real custom-Capability checklist

- Use one directly declared dataclass with a stable non-colliding serialization name.
- Keep declarative fields deterministic and serializable. Supply first-party feature clients and overrides through their documented typed `RunBindings` fields; only invocation-policy and MCP Capabilities belong in `RunBindings.capabilities`.
- Let trusted Host code construct the exact `CapabilityTypeCatalog`; do not discover classes from package metadata or serialized import targets.
- Treat catalog membership as availability and `AgentSpec.capabilities` as selection.
- Use direct definition composition when the caller already owns a trusted concrete instance.
- Use a Harness plugin only when behavior must wrap the complete semantic-input-to-result boundary.

### Middleware and feature with Host bindings

[`demo_web.py`](src/a13n_plugin_examples/demo_web.py) composes `RunRecorderPlugin` middleware with one definition-owned `WebCapability`. The Host selects this reserved first-party feature in the Agent definition and supplies the current `WebBinding` through `RunBindings.web`, not a second Capability or a plugin contribution:

```python
executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,
    plugins=(RunRecorderPlugin(plugin_id="recorder-web"),),
    capabilities=(
        WebCapability(
            WebConfiguration(
                search=WebSearchConfiguration(mode="off"),
                scrape=WebScrapeConfiguration(mode="off"),
            ),
        ),
    ),
)
result = await executable.run(
    "Fetch the offline fixture.",
    bindings=RunBindings.embedded(
        web=WebBinding(client=provider, policy=provider),
    ),
)
```

Run the complete offline path with:

```bash
uv run --locked python -m a13n_plugin_examples.demo_web
```

It invokes the standard `fetch` tool against a fixture-only client and prints `Host-owned offline Web response`. No network, credentials, or Environment are needed. The definition selects feature behavior, middleware observes the completed Run, the Host owns provider lifetime, and fresh binding values never enter continuation State. The same separation applies to Media, Documents, file-media understanding, Skill selection, task state, and client-tool overrides; see the [Capability guide](../../docs/a13n-harness/capabilities.md).

## Harness Plugin

The distribution registers one package factory:

```toml
[project.entry-points."a13n_harness.plugins"]
"example.run-recorder" = "a13n_plugin_examples.harness:RunRecorderPluginFactory"
```

[`harness.py`](src/a13n_plugin_examples/harness.py) contains:

- a strict package-owned `RunRecorderConfiguration` model for the `configuration` payload;
- `RunRecorderPluginFactory`, which receives standardized plugin key/ID, package parameters, and namespaced extensions through `HarnessPluginFactoryContext`;
- `RunRecorderPlugin`, with stable ordering and fresh exact-type run binding;
- small immutable observation records in a demo-only in-memory sink that does not retain prompts or model output.

[`records.py`](src/a13n_plugin_examples/records.py) holds neutral result types so metadata discovery and demo imports do not import the plugin target early.

### Configured installed mode

[`harness-plugins.yaml`](src/a13n_plugin_examples/harness-plugins.yaml) is the complete configuration-file example:

```yaml
schema_version: "1"
plugins:
  - plugin_id: recorder-entrypoint
    plugin_key: example.run-recorder
    enabled: true
    configuration:
      count_events: true
  - plugin_id: recorder-disabled
    plugin_key: example.run-recorder
    enabled: false
    configuration:
      count_events: false
```

`plugin_id`, `plugin_key`, and `enabled` belong to the Harness envelope. `count_events` is a real package-owned parameter validated by `RunRecorderConfiguration` and used directly by the plugin. Disabled entries are neither imported on their own nor created.

A Host does not need a file. It can pass the same data schema directly:

```python
context = HarnessBuildContext.from_configuration(
    {
        "schema_version": "1",
        "plugins": [
            {
                "plugin_id": "recorder-entrypoint",
                "plugin_key": "example.run-recorder",
                "enabled": True,
                "configuration": {"count_events": True},
            }
        ],
    }
)
builder = HarnessBuilder(build_context=context)
```

[`run_harness_entrypoint_demo()`](src/a13n_plugin_examples/demo_harness.py) uses the optional file form and loads the YAML with `HarnessBuildContext.from_file()`. `HarnessBuilder` selects the installed `example.run-recorder` entry point and builds the concrete middleware without exposing the factory or plugin object in `AgentDefinition`:

```bash
uv run plugin-example-harness-entrypoint
```

A hosted create-and-run or create-and-stream path can keep the deployment default disabled and opt one executable construction in without rewriting configuration:

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=false
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

```python
builder = HarnessBuilder(configured_plugins_enabled=True)
```

`True` overrides only the enable switch; source precedence, validation, selected-key import, and failure behavior remain identical. The choice is fixed when the Agent is built because plugins may contribute Capabilities, tools, settings, instructions, and hooks. `run()` and `stream()` therefore do not expose an unsafe partial late toggle.

### Long-lived Host plugin directory

A Host may install a complete plugin distribution into a fresh directory that is not yet searchable, publish that directory on Python's package search path while the Host remains alive, invalidate Python's import caches, and construct a new builder. The new builder sees the current entry-point metadata. Existing builders retain their selected factories and create fresh plugin instances from them on later builds; existing executables retain their already constructed plugin graphs. This supports adding plugins without rebuilding the Host image or restarting its Python process, but it does not define in-place reload or replacement of an already imported module.

Use `PYTHONPATH` or `sys.path` for Python packages; the shell executable `PATH` is unrelated. Never install incrementally into a directory already exposed to the running process. The completed distribution must include `.dist-info` entry-point metadata rather than only the import module. See the [Harness plugin guide](../../docs/a13n-harness/plugins.md#use-a-host-managed-plugin-directory) for the complete Host sequence and rollout boundaries.

### Explicit code mode

[`run_harness_code_demo()`](src/a13n_plugin_examples/demo_harness.py) constructs `RunRecorderPlugin` directly. This mode can inject a Python observation sink that cannot be represented as JSON:

```python
observations: list[RunObservation] = []
plugin = RunRecorderPlugin(
    plugin_id="recorder-code",
    observation_sink=observations,
)
```

```bash
uv run plugin-example-harness-code
```

Both paths use an offline `FunctionModel` and produce a deterministic result apart from the generated run ID and positive event count:

```text
selection mode: entrypoint
plugin id: recorder-entrypoint
run id: <generated run ID>
output: offline model response
observed status: completed
observed events: <positive count>
```

Code mode reports `selection mode: code` and `plugin id: recorder-code`.

### Real middleware checklist

- Give every concrete instance a unique, stable, non-blank `plugin_id`.
- Keep the package factory no-argument and side-effect free.
- Accept standardized identity and extensions through `HarnessPluginFactoryContext`; validate only the package-owned `configuration` payload.
- Keep the Agent-bound plugin reentrant.
- Return an exact-type run-isolated instance from `for_run()` when run state is mutable.
- Express ordering through `PluginOrdering`.
- Call `call_next(exchange)` at most once and put cleanup in the returned async iterator.
- Do not retain credentials, raw prompts, model output, or unbounded event payloads.
- Use a production-owned async sink when observations leave the process.

The example list intentionally has no long-term retention policy; it only keeps each record small. A long-lived application must inject a sink with its own bounded retention, backpressure, and export policy.

Harness middleware is trusted in-process code. Behavior inside the Pydantic Agent loop belongs in a Capability rather than a wider `wrap_run()` layer.

## Tests and Packaging

[`test_environment.py`](tests/test_environment.py) verifies:

- metadata-only discovery and selected-only import;
- installed and explicit factory modes;
- pre-entry factory inertness;
- package configuration validation;
- real two-binding routing through both modes.

[`test_environment_extension.py`](tests/test_environment_extension.py) verifies:

- metadata-only discovery and selected-only import;
- installed and explicit factory modes;
- package configuration validation;
- marker availability during the aggregate scope and cleanup before provider teardown.

[`test_harness.py`](tests/test_harness.py) verifies:

- metadata-only discovery and selected-only import;
- configured YAML factory and explicit concrete-object modes;
- standardized context and package parameter validation;
- both complete offline demos;
- isolated state across concurrent logical runs.

The fast gate validates locks, style, and types:

```bash
make examples-check
```

The complete gate also runs focused tests and every smoke path, then builds each wheel and source distribution:

```bash
make examples-check-all
```

For a shorter loop inside this directory:

```bash
uv run --locked ruff check --no-fix .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```
