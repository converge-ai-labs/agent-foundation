# Installed Harness Plugins

## Purpose and Ownership

a13n Service executes trusted Harness plugins installed in its immutable build artifact. Plugin code and dependencies change only by building and rolling out a new Service image. Agent editing selects installed plugin keys and supplies configuration; it never uploads, installs, activates, or replaces Python code.

[Agent Management](28-agent-management.md) owns authoring, immutable Revisions, Run overrides, and effective configuration. [Scheduling and Recovery](13-run-attempt-scheduling-and-recovery.md) owns RunAttempt leases, fences, build observations, and service drain. [Harness Runtime Integration](14-harness-runtime-integration.md) owns reconstruction and state restoration. The Harness factory catalog owns entry-point discovery, configuration validation, and fresh plugin construction.

## Build and Process Composition

A distribution packages plugins and their jointly compatible dependencies in the Service image. Custom plugins use the same path through a custom image. Dependency resolution and installation happen during the build, never while admitting or executing a Run.

`A13N_SERVICE_PLUGIN_KEYS` selects installed `a13n_harness.plugins` entry-point keys for the deployment. Its default is an empty tuple. Every role loads the selected catalog once during startup, off the async event loop. Missing keys, duplicate keys, invalid factories, and import failures prevent startup and readiness. A trusted distribution can instead supply an explicit immutable `HarnessPluginFactoryCatalog` through its component composition.

Control and Connectivity use that catalog to validate Agent configuration. Worker uses the same catalog contract to reconstruct Agents. An all-in-one process shares one catalog across its role contributions. The catalog and Python module bindings remain fixed until process exit. Service disables HarnessBuilder configured-plugin loading explicitly; `A13N_HARNESS_PLUGIN_CONFIG_*` cannot append plugins or affect Run execution or recovery.

One Worker process owns one bounded `WorkerExecutionLoop`. Each successful claim starts one executor async task using that process's catalog. There is no plugin subprocess, Supervisor, IPC protocol, per-version process cache, runtime materializer, or per-plugin execution pool. Environment maintenance retains its separately bounded Worker-owned loop.

A deployment uses the same reviewed plugin selection across its roles. During a rolling update, deploy compatible Worker capacity before Control starts accepting configuration requiring new code. Operator-managed deployment ordering and readiness own this coordination; Service has no all-Worker staging or catalog-cutover protocol.

## Configuration and Recovery

```python
class PluginSelection:
    instance_name: str
    plugin_key: str
    config: JsonObject
```

`plugin_key` identifies one selected installed factory. `instance_name` identifies one plugin occurrence within an Agent and remains stable in its frozen configuration. Several occurrences may use the same factory with different configurations. Agent selection contains no deployment mode, PluginVersion ID, artifact reference, or package-version selector.

Agent Revision creation validates and normalizes configuration through the factory. The authored selection remains in `AgentConfig.plugins`; the normalized result is frozen as `resolved_plugins`. Run acceptance preserves the normalized selection for an unchanged Revision or validates a supplied override, then freezes the complete effective configuration and child graph. Existing Agent authorization owns these operations. There is no separate executable-code deployment permission in the management API.

Every execution attempt verifies the frozen effective-config digest, requires the selected factory, and validates the frozen configuration against the current factory. Validation must preserve the frozen normalized JSON exactly; accepting new defaults or silently dropping fields is incompatible. Only after this validation does reconstruction create fresh plugin instances and restore state through the owning Harness and Capability codecs.

A compatible new Worker can resume an old Run, including a Run that waited for feedback through a deployment. The Run retains its Agent Revision, effective configuration, input and continuation state. It does not pin plugin code, Python dependency versions, or a historical Worker environment. Each RunAttempt records the actual `worker_build_id` and process `worker_id`; these observations grant no lease authority and are not compatibility proofs.

Harness state envelopes and component state remain subject to their existing schema and codec validation. Plugin authors must reject state they cannot interpret. Configuration validation and successful decoding do not prove behavioral equivalence; deployments test required upgrade paths. An incompatible build requires an explicit state migration or completion of affected work with compatible capacity before replacement. Service never converts incompatible state by substituting a default or an empty checkpoint.

## Rolling Update Example

An Agent uses `support.audit` with a frozen configuration. Its Run is waiting for approval when build B replaces build A:

1. Build B packages the new plugin and dependencies and starts with a valid installed catalog.
2. Build A stops claiming new work and drains under the ordinary lease and handoff rules. Live Attempts retain renewal until completion, committed handoff, or the drain deadline.
3. When approval arrives, a compatible Worker claims the next Attempt through the same transactional lease and fence.
4. Build B validates the frozen configuration and restores the retained Harness and component state before continuing execution. The Attempt records build B; the Run's configuration remains unchanged.

If build B cannot validate the configuration or restore the state, execution fails through the existing bounded preparation or recovery error path. It does not download build A's plugin, create a historical interpreter, or silently reinterpret the state. Forced shutdown still relies on actual lease expiry for takeover; rolling deployment never bypasses fencing.

## Management and Persistence Boundaries

Service exposes no Plugin upload, version publication, activation, deactivation, archival, or plugin-operation receipt routes. Installed plugins are build content rather than mutable management resources. There are no Plugin, PluginVersion, runtime-lock, runtime-command, runtime-resolution, or active-catalog database tables.

Agent Revisions, effective configurations, Runs, Attempts, and checkpoints carry no plugin runtime-lock digest or runtime-mode field. Plugin Wheels and dependency archives are not published to the Service object store and have no Service retention or garbage-collection lifecycle. Image storage and retention belong to the deployment toolchain.

## Security and Trade-offs

Plugins execute with Worker authority in the trusted Service process. The distribution author and image deployment operator control executable code. Agent configuration, model output, and possession of a plugin key cannot change that code. Factory validation and construction retain the Harness contract's bounded safe errors.

All plugins in an image must share a compatible dependency environment. Updating one plugin requires an image rollout. This model does not provide per-plugin process crash containment or simultaneous incompatible dependency environments within one Worker. Scaling adds Worker replicas under the existing global relational ownership rules.

These constraints remove runtime dependency solving, uploaded-code retention, historical environment rebuilding, distributed catalog activation, and nested process-capacity management. Memory and database connections follow the ordinary Service process model; plugin memory still contributes to that process's footprint. No exact-code replay or unchanged behavior across releases is promised.

## Invariants

1. Executable plugin code and dependencies are immutable for a process lifetime and change only through deployment.
2. Agent configuration selects an installed key and instance name, never Python package bytes or versions.
3. One Worker uses one catalog and one bounded execution loop; plugin configuration does not create execution processes.
4. New Attempts may use compatible new code while preserving frozen configuration and state semantics.
5. Build identity is observational; leases, fences, and checkpoint compare-and-swap remain authoritative.
6. Missing plugins, incompatible configuration, and invalid state fail explicitly before resumed Harness effects.
7. No runtime installation, live module replacement, historical-lock fallback, or parallel old plugin mode remains.
