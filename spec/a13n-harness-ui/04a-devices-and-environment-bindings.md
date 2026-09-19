# Devices and Environment Bindings

## Design Position

Harness UI stores Device connections and Project/Thread working-directory selections. Each Run captures its bindings and receives fresh Environment adapters. EIP Sessions are internal execution resources, not user-managed Project or Thread resources.

[Projects and Threads](04-projects-threads-and-environments.md) owns local roots, sticky configuration and admission. [EIP](../a13n-envd/README.md) owns Device discovery, Sessions and resource cleanup.

## Devices

`devices/*.yaml` uses the ordinary accepted configuration generation. Each resource contains a stable Host Device ID, expected envd `device_id`, display name, transport configuration and authentication. Manually configured authentication references a Host environment variable or saved API key. Paired authentication stores only the daemon credential digest and its revocation state and requires reverse WebSocket transport. HTTP names an origin; reverse WebSocket names an expected attachment identity. Raw credentials, sockets, Session selectors and observed generations are not resource configuration.

### Self-registration

The App implements the shared [Host pairing protocol](../a13n-harness/08a-environment-providers.md#host-pairing). Only `POST /api/envd/pair` admits a daemon's narrow pairing credential without browser authentication. Pending requests grant no Device access and remain bounded, expiring process-local state. Their safe challenge is visible to authenticated users. Approval publishes an ordinary Device resource through validated configuration mutation; retried approval or polling resolves the approved resource, including after App restart. A native identity already registered with a different credential is a conflict, not implicit credential replacement.

The approved credential authenticates only that Device's reverse WebSocket attachment. Human management APIs retain normal browser authentication. Device discovery and Run preparation use the same digest-scoped connection without requiring recoverable credential bytes. Each daemon process selects one Host; independent envd instances on one physical machine have independent registration identities.

**Revoke** persists revoked trust before retiring its live connection. It denies later attachment and use through captured configurations, including after restart, and does not delete remote files, Project/Thread selections or Run history. Revocation is distinct from forgetting local configuration. A failed or revoked credential never triggers automatic replacement or approval.

Registration and connection checks are trusted Host actions. Catalog loading performs no connection or filesystem I/O. Online status and Device info are runtime observations. Missing or unavailable Devices never fall back to local execution. Paths use the selected Device's path format, not the UI server's filesystem.

## Directory Selection

The App obtains Device info, including the resolved default working directory and directory-discovery capability, without opening a Session. WebUI calls the App's authenticated API; the App invokes EIP `device.describe` and `directory.list`. Directory browsing has finite request bounds and ends on cancellation or timeout without retaining a Session.

Users select the default directory, enter a known path, or browse when discovery is enabled. Disabled discovery hides browsing but preserves default and explicit-path selection. A missing/inaccessible directory produces a visible failure, never silent replacement or creation. Directory selection creates no Run and reserves no future filesystem availability.

The accepted Project/Thread selection stores an explicit Device and working directory. It does not retain a symbolic "use current Device default" choice. Later daemon-default changes do not redirect existing selections. Users can explicitly select the new default.

## Bindings and Defaults

A Thread retains `environment_profile_id` for local Project roots and Thread files. Its ordered `environment_bindings` collection contains `device_id`, `working_directory`, a unique Run-local alias and an action ceiling. Selecting a Device and path requires no separate Environment-definition resource.

`default_environment` names one alias in the complete mount set. Local-only selections retain their workspace or projectless Thread-files default. Remote-only and mixed sets record the default explicitly. Local workspace and Host auxiliary aliases are reserved against collision. An explicit empty collection removes added bindings; a Run-only local-profile override preserves them.

Project creation defaults and Thread patches use existing precedence and expected-version rules. Deferred-response admission retains the selected continuation's bindings and default unless explicitly patched. A Project may have no local roots when it selects remote bindings; CLI cwd matching considers only actual local roots. Projectless Threads may also use a remote default while retaining Host-managed Thread files.

A working directory supplies relative-path and omitted-cwd defaults. File operations may address other paths on the same Device under the action ceiling and native permissions. Selecting a working directory creates no filesystem sandbox.

## Admission and Execution

Before returning an admission receipt or scheduling preparation, the App captures:

- accepted resource generation and Thread configuration version;
- Project roots and local profile;
- selected Device identities and connection configurations without credential bytes;
- explicit working directories, aliases, effective action ceilings and default;
- selected Run Extensions.

Preparation uses only captured selections. Current credentials resolve through captured credential references at use time. Each envd binding opens a fresh Session with its captured working directory. Repeated operations reuse that Session within the same execution owner. Concurrent Runs, independent children and identical directory paths do not share initialized Sessions.

Short same-runtime reattachment may reuse the same live Session and generation. Expired Sessions, replacement execution owners and application restarts require fresh Sessions; native handles and uncertain effects are not recovered or replayed. Session close preserves the shared Device connection and working files.

New children inherit the parent's captured selections; existing child Threads resume from their own selections. Remote adapters use virtual aggregate routes, not Host-path-preserving routing. Skills and file guidance use the selected Environment. Configuration and user-Skill mounts stay Host-only. Remote shell execution does not implicitly receive Thread files; cross-mount copying uses Environment file operations.

## State and Connection Ownership

Environment-state lookup/publication uses the Thread, alias, normalized Provider configuration, Device identity/backend and working directory. Presentation-only changes do not affect binding identity. Local roots retain their existing equivalent key.

Device connections are App-owned process-local resources. Session selectors, daemon generations, keepalive tasks and native process/output references never enter persisted continuation. App shutdown closes owned Sessions before local daemons or connection managers. It does not stop external daemon infrastructure.

## WebUI Behavior

**Add environment** selects a Device and working directory. The surface shows alias, Device, path, default and availability; it exposes no Session creation, reuse or cleanup controls. Existing Project path selection and remote directory selection use the same saved-binding flow.

Edits use resource-save and expected-version Thread commands. Active Runs display captured selections separately from next-Run settings. Device online status and per-binding readiness remain distinct; a directory failure does not make the Device offline. Browser clients receive neither daemon credentials nor EIP resource selectors.

**Remove environment** edits the Project or Thread selection without contacting the Device. Removing the selected default requires an explicit replacement from the remaining working mounts in the same save. **Forget** in Settings removes a user-owned Device connection or local Environment profile through ordinary configuration-source deletion; remaining configuration references must be repaired first. It does not delete remote files or infrastructure, close execution-owned Sessions, cascade into Thread selections, or rewrite captured Runs and history. A Thread retaining a missing resource remains inspectable and supports an explicit valid replacement before its next Run.
