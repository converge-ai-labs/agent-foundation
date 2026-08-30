# Integration Package Examples

This standalone project demonstrates the supported configuration and direct-code composition modes for the Agent Harness package-extension boundaries. Every path is runnable offline and covered by focused tests.

## Composition Matrix

| Boundary                  | Installed entry-point mode                                                                                                                     | Explicit code mode                                                                                         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Environment provider      | Select an `EnvironmentProviderFactory` from `a13n_environment_provider.providers`, then construct a Provider from an exact specification       | Supply an `EnvironmentProviderFactory` object directly, then construct the same Provider                   |
| Environment run extension | Select an `EnvironmentRunExtensionFactory` from `a13n_harness.environment_run_extensions`, then call `create_extension()`                      | Supply an `EnvironmentRunExtensionFactory` object directly, then call the same `create_extension()` method |
| Harness middleware        | Let a `HarnessBuildContext` load preferred YAML or JSON, select enabled `HarnessPluginFactory` entries, and apply fresh instances during build | Construct an `AbstractHarnessPlugin` directly and place it in `AgentDefinition.plugins`                    |

Entry-point metadata provides only a stable key and import target. Harness middleware configuration uses the Harness-owned versioned envelope; YAML is preferred for files, JSON is supported for files and inline environment values, and each plugin package owns only the typed `configuration` payload.

Both entry-point paths are explicit and lazy:

1. metadata discovery does not import target modules;
2. Environment callers select exact provider or run-extension keys, while the Harness builder selects only enabled document keys;
3. catalog construction imports only those selected targets;
4. factory output enters the same ordinary concrete-object path used by code mode.

Package presence is availability, not authorization. Neither catalog accepts an arbitrary import path or mutates a process-global registry.

## Quick Start

From the repository root:

```bash
make examples-check-all
```

From this directory:

```bash
uv sync --locked
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
a13n-environment-provider = { path = "../../packages/agent-environment-provider", editable = true }
a13n-harness = { path = "../../packages/agent-harness", editable = true }
```

A standalone plugin distribution should remove those development sources and declare the released Provider and Harness ranges it supports.

## Environment Provider Factory

The distribution registers one package factory:

```toml
[project.entry-points."a13n_environment_provider.providers"]
"example.workspace" = "a13n_plugin_examples.environment:WorkspaceEnvironmentProviderFactory"
```

[`environment.py`](src/a13n_plugin_examples/environment.py) contains:

- a strict package-owned schema-version-1 `WorkspaceEnvironmentConfiguration` model;
- an exact process-local `WorkspaceEnvironmentRuntime` collaborator;
- a no-argument, side-effect-free `WorkspaceEnvironmentProviderFactory`;
- a complete `WorkspaceEnvironmentProvider` and single-entry Resource;
- fresh `DirectLocalEnvironmentAttachment` values for Harness transfer.

Specification validation, factory construction, and Provider construction perform no filesystem I/O. `create()` and `resume()` validate the selected existing workspace; neither creates or owns it. Harness mount and provider-binding cleanup, Resource scope cleanup, and explicit Provider destroy remain separate operations.

### Installed entry-point mode

[`run_environment_entrypoint_demo()`](src/a13n_plugin_examples/demo_environment.py) discovers metadata, verifies that `example.workspace` is installed, selects only that key, and constructs two Providers from Host-supplied exact specifications and fresh runtime collaborators.

```bash
uv run plugin-example-environment-entrypoint
```

### Explicit code mode

[`run_environment_code_demo()`](src/a13n_plugin_examples/demo_environment.py) imports and supplies `WorkspaceEnvironmentProviderFactory()` directly. It still invokes the same catalog Provider-construction path, so schema, runtime, state, and lifecycle validation remain identical.

```bash
uv run plugin-example-environment-code
```

Both paths create two Resources, acquire and transfer two fresh attachments, construct one `EnvironmentRuntime` with an atomic initial mount set, enter and activate the runtime, and verify default and explicitly qualified routing. The source Provider then demonstrates explicit durable lifecycle calls: reconcile create as running, resume the exact validated state into a fresh Resource and attachment, reconcile resume as running, destroy, and reconcile authoritative absence. The example Provider advertises no pause mode, so it reports that pause is unsupported rather than silently changing lifecycle semantics:

```text
selection mode: entrypoint
selected provider: example.workspace
active aliases: source, docs
default route: source workspace
docs route: documentation workspace
durable lifecycle: running -> running -> absent
pause supported: False
```

Code mode prints the same result with `selection mode: code`. A provider that advertises `EnvironmentPauseMode.FULL` or `FILESYSTEM` adds the explicit pause transition before resume; the complete Host operation sequence is documented in the [Environment guide](../../docs/agent-harness/environments.md#manage-a-durable-provider-lifecycle-explicitly).

### Real provider checklist

- Use one stable namespaced entry-point name and return the same value from `provider_key()`.
- Keep configuration, factory, and Provider construction strict, bounded, and side-effect free.
- Support exact schema versions without fallback or shape inference.
- Accept current credentials and client factories only through a typed process-local runtime collaborator.
- Tie every lifecycle call to a Host-generated operation identity and resource correlation.
- Return provider-owned state before resource-scope entry and validate it exactly on resume, destroy, and reconciliation.
- Issue only fresh supported attachments while a single-entry Resource scope is active.
- Treat `reconcile()` as bounded read-only inspection of one exact prior operation.
- Keep mount names, permission ceilings, desired mount definitions, attachment transfer, durable storage, authorization, and scheduling under Host control.

The example uses the public `DirectLocalEnvironmentAttachment` backend to stay focused on provider packaging and lifecycle. A remote sandbox provider issues an `EIPEnvironmentAttachment`; it does not implement another Harness mount or provider-neutral operation layer.

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

### Installed entry-point mode

[`run_environment_extension_entrypoint_demo()`](src/a13n_plugin_examples/demo_environment_extension.py) discovers metadata, selects only `example.workspace-marker`, creates one configured instance, registers it on `create_environment_runtime()`, and exercises the complete scope:

```bash
uv run plugin-example-environment-extension-entrypoint
```

### Explicit code mode

[`run_environment_extension_code_demo()`](src/a13n_plugin_examples/demo_environment_extension.py) supplies `WorkspaceMarkerExtensionFactory()` directly without scanning installed metadata, then calls the same catalog method:

```bash
uv run plugin-example-environment-extension-code
```

Both paths verify that the marker is available after aggregate activation and absent after aggregate close:

```text
selection mode: entrypoint
selected extension: example.workspace-marker
extension id: marker-entrypoint
marker content: entrypoint:run-extension-example
marker removed: True
```

Code mode reports `selection mode: code` and `extension id: marker-code`.

A run extension spans the complete `EnvironmentRuntime`, not one provider resource. It enters once in registration order after state restore, remains entered across mount mutations, and exits in reverse order while provider-neutral Environment operations are still available. It receives no model, `AgentContext`, Harness plugin context, or runtime mutation authority.

### Real run-extension checklist

- Give every aggregate instance a unique, stable, non-blank `extension_id`.
- Keep factory construction and `create_extension()` side-effect free.
- Validate the package-owned JSON configuration with a strict schema.
- Acquire all cleanup-producing resources inside `bind()`.
- Use only provider-neutral `Environment` operations when touching Environment resources.
- Make exit finite and clean every owned resource even when the run failed.
- Do not expect runtime mount mutations to rebind the extension.
- Use a Harness plugin for input/result middleware and a Capability for Agent-loop behavior instead.

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

Use `PYTHONPATH` or `sys.path` for Python packages; the shell executable `PATH` is unrelated. Never install incrementally into a directory already exposed to the running process. The completed distribution must include `.dist-info` entry-point metadata rather than only the import module. See the [Harness plugin guide](../../docs/agent-harness/plugins.md#use-a-host-managed-plugin-directory) for the complete Host sequence and rollout boundaries.

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
