# Installed Harness Plugins

## Purpose and Ownership

a13n Service executes trusted Harness plugins installed in an immutable Worker build artifact. Plugin code and dependencies change only by building and rolling out new Worker capacity. Agent editing selects installed plugin keys and supplies configuration; it never uploads, installs, activates, or replaces Python code.

[Agent Management](28-agent-management.md) owns authoring, immutable Revisions, Run overrides, and effective configuration. [Scheduling and Recovery](13-run-attempt-scheduling-and-recovery.md) owns RunAttempt leases, fences, build observations, and service drain. [Harness Runtime Integration](14-harness-runtime-integration.md) owns reconstruction and state restoration. The Harness factory catalog owns entry-point discovery, configuration validation, and fresh plugin construction.

## Build and Process Composition

A distribution packages plugins and their jointly compatible dependencies in its Worker image. Control and Connectivity can use the standard Service image without business plugins. Custom plugins use the same path through a custom image. Dependency resolution and installation happen during the build, never while admitting or executing a Run.

`A13N_SERVICE_PLUGIN_KEYS` selects installed `a13n_harness.plugins` entry-point keys for Worker execution. Its default is an empty tuple. Only `worker` and `all` load the selected catalog once during startup, off the async event loop. Missing keys, duplicate keys, invalid factories, and import failures prevent their startup and readiness. Control and Connectivity never discover or import these factories. A trusted distribution can instead supply an explicit immutable `HarnessPluginFactoryCatalog` through its component composition.

Control and Connectivity validate bounded selection structure and authored JSON without a catalog. Worker uses its catalog to prepare plugin configuration and reconstruct Agents. An all-in-one process supplies its catalog exclusively to Worker execution. The catalog and Python module bindings remain fixed until process exit. Service disables HarnessBuilder configured-plugin loading explicitly; `A13N_HARNESS_PLUGIN_CONFIG_*` cannot append plugins or affect Run execution or recovery.

One Worker process owns one bounded `WorkerExecutionLoop`. Each successful claim starts one executor async task using that process's catalog. There is no plugin subprocess, Supervisor, IPC protocol, per-version process cache, runtime materializer, or per-plugin execution pool. Environment maintenance retains its separately bounded Worker-owned loop.

A plugin update requires only a Worker rollout. Control and Connectivity continue admitting structurally valid configuration without knowing the available plugin builds. Operators supply compatible Worker capacity before submitting configuration that requires new code; a Worker missing a selected factory fails preparation rather than rerouting or fetching code. Service has no all-Worker staging, catalog-cutover, or build-selection protocol.

## Configuration and Recovery

```python
class PluginSelection:
    instance_name: str
    plugin_key: str
    config: JsonObject


class PreparedAgentPlugins:
    plugins: tuple[PluginSelection, ...]
    children: dict[AgentRevisionId, PreparedAgentPlugins]
```

`plugin_key` identifies one selected installed factory. `instance_name` identifies one plugin occurrence within an Agent and remains stable in its frozen configuration. Several occurrences may use the same factory with different configurations. Agent selection contains no deployment mode, PluginVersion ID, artifact reference, or package-version selector.

Agent Revision creation and Run admission retain authored plugin selections without invoking factories, applying defaults, or asserting installed-code availability. `AgentConfig.plugins` owns the Revision's selection; `EffectiveAgentConfig.plugins` stores the accepted selection for each graph node. There is no second normalized selection in the Revision. Existing Agent authorization owns these operations. Missing factories and invalid business configuration are Worker preparation failures, not synchronous admission errors.

The first preparing Worker normalizes every selected plugin in the complete accepted Agent graph. It durably stores one `PreparedAgentPlugins` tree in the Run state before constructing plugin instances or entering the Harness. Each tree node contains normalized `plugins` and `children` keyed by the accepted child Revision IDs. The tree is absent until preparation succeeds, and immutable once present. The accepted effective configuration and its digest remain unchanged.

[Run persistence](12-run-persistence.md#plugin-configuration-preparation) owns the conditional publication. Preparation changes neither Harness state nor `checkpoint_seq`: a prepared Run at sequence zero still receives its initial accepted input. A write failure or uncertain result never permits business execution. A replacement Attempt reads the authoritative complete object and reuses any saved preparation. Configuration validators must be deterministic, side-effect-free transformations of authored JSON; failed preparation can invoke them again.

Every execution attempt verifies the frozen effective-config digest, the prepared tree's exact graph and ordered plugin identities, and its normalized configuration against the current factory. Validation must preserve the saved JSON exactly, including JSON value types. Applying new defaults or silently dropping fields is incompatible. Only then may reconstruction create fresh plugin instances.

Preparation is frozen at the first successful publication, not at Run acceptance. A Run accepted before an upgrade but never prepared uses the first successful preparing Worker's defaults. Same-Run recovery always retains preparation. Waiting feedback, continuation, and fork retain it when the complete authored plugin graph is unchanged; explicitly changing that graph prepares a new tree for the new Run. A change to instructions or Model configuration alone does not reset plugin preparation. Explicit Retry copies the failed Run's preparation while its Harness input/state initialization retains the Retry contract. An asynchronous child receives the corresponding subtree from its parent's prepared graph; retained child and automatic-result continuations preserve their own source preparation.

A compatible new Worker can resume an old Run, including a Run that waited for feedback through a deployment. The Run retains its Agent Revision, effective configuration, input and continuation state. It does not pin plugin code, Python dependency versions, or a historical Worker environment. Each RunAttempt records the actual `worker_build_id` and process `worker_id`; these observations grant no lease authority and are not compatibility proofs.

Harness state envelopes and component state remain subject to their existing schema and codec validation. Plugin authors must reject state they cannot interpret and read/validate retained state before performing business effects. Capability state validation occurs when the plugin reads its state; Service cannot preflight arbitrary Python behavior or infer semantic changes from unchanged JSON schemas. Configuration validation and successful decoding do not prove behavioral equivalence; deployments test required upgrade paths. An incompatible build requires an explicit state migration or completion of affected work with compatible capacity before replacement. Service never converts incompatible state by substituting a default or an empty checkpoint.

## Rolling Update Example

An Agent uses `support.audit` with a frozen configuration. Its Run is waiting for approval when build B replaces build A:

1. Build B packages the new plugin and dependencies and starts Worker capacity with a valid installed catalog. Control and Connectivity remain deployed unchanged.
2. Build A stops claiming new work and drains under the ordinary lease and handoff rules. Live Attempts retain renewal until completion, committed handoff, or the drain deadline.
3. When approval arrives, a compatible Worker claims the next Attempt through the same transactional lease and fence.
4. Build B validates the frozen configuration and restores the retained Harness and component state before continuing execution. The Attempt records build B; the Run's authored and prepared configurations remain unchanged. If build A saved `timeout=30` and build B defaults to `60`, this execution still receives `30`.

If build B cannot validate the configuration or restore the state, execution fails through the existing bounded preparation or recovery error path. It does not download build A's plugin, create a historical interpreter, or silently reinterpret the state. Forced shutdown still relies on actual lease expiry for takeover; rolling deployment never bypasses fencing.

## Management and Persistence Boundaries

Service exposes no Plugin upload, version publication, activation, deactivation, archival, or plugin-operation receipt routes. Installed plugins are build content rather than mutable management resources. There are no Plugin, PluginVersion, runtime-lock, runtime-command, runtime-resolution, or active-catalog database tables.

Agent Revisions, effective configurations, Runs, Attempts, and checkpoints carry no plugin runtime-lock digest or runtime-mode field. Plugin Wheels and dependency archives are not published to the Service object store and have no Service retention or garbage-collection lifecycle. Image storage and retention belong to the deployment toolchain.

## Security and Trade-offs

Plugins execute with Worker authority in the trusted Service process. The distribution author and image deployment operator control executable code. Agent configuration, model output, and possession of a plugin key cannot change that code. Factory validation and construction retain the Harness contract's bounded safe errors.

All plugins in a Worker image must share a compatible dependency environment. Updating one plugin requires a Worker image rollout. This model does not provide per-plugin process crash containment or simultaneous incompatible dependency environments within one Worker. Scaling adds Worker replicas under the existing global relational ownership rules.

First preparation adds one conditional write of the bounded Run state object; its cost scales with that object's size. Recovery validates retained configuration without another preparation write. Control admission performs no plugin import or factory validation. These constraints remove runtime dependency solving, uploaded-code retention, historical environment rebuilding, distributed catalog activation, and nested process-capacity management. Memory and database connections follow the ordinary Service process model; plugin memory still contributes to that process's footprint. No exact-code replay or unchanged behavior across releases is promised.

## Invariants

1. Executable plugin code and dependencies are immutable for a process lifetime and change only through deployment.
2. Agent configuration selects an installed key and instance name, never Python package bytes or versions.
3. One Worker uses one catalog and one bounded execution loop; plugin configuration does not create execution processes.
4. New Attempts may use compatible new code while preserving frozen configuration and state semantics.
5. Build identity is observational; leases, fences, and checkpoint compare-and-swap remain authoritative.
6. Missing plugins and incompatible configuration fail before Harness entry; state decoding failures remain explicit at the owning codec boundary.
7. Configuration preparation never consumes input, and its durable publication precedes plugin construction and business execution.
8. Control and Connectivity require no installed business-plugin code.
9. No runtime installation, live module replacement, historical-lock fallback, or parallel old plugin mode remains.
