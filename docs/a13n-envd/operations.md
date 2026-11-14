---
title: Operate Sessions and retained output
sidebarTitle: Sessions and output
description: Manage daemon readiness, Session lifetime, retained output capacity, and transfers.
---

Open a Session for each scope of work, read the output you need, and close the Session when that scope ends. A Session owns its commands, transfers, retained output, and operation history. One daemon serves multiple Sessions; the launcher manages the daemon's lifetime.

## Establish readiness

1. Create the private runtime parent and configure the [daemon](configuration.md): Device identity, default working directory, executable policy, limits, and carrier.
2. Start the daemon and initialize a Device connection. Initialization returns the Device descriptor without opening a Session.
3. Inspect the descriptor's methods and boundary. Browse Device directories if you need to choose a working directory.
4. Open a Session with that working directory and the required methods, then wait for readiness before dispatching work.
5. Save the Device generation, Session identity, and operation receipts together so later reads and reconciliation address the same work.

Keep the owning scope alive with Session keepalive. The default idle lifetime is one hour with no maximum lifetime; use the Session descriptor’s effective lifecycle policy. Reattachment to the same Session is explicit and requires the same generation and a newer owner epoch before its deadline. Reconnecting the carrier alone does not restore Sessions or in-flight operations.

Close the Session when its scope ends. Cleanup leaves sibling Sessions and workspace files intact. Session admission and aggregate Device quotas limit concurrency; failed cleanup continues to consume capacity until reclamation is confirmed.

The working directory sets path defaults. Access to other Device paths follows the execution account and Sandbox grants. Use separate outer accounts, containers, or sandboxes for mutually untrusted workloads; see [execution boundaries](isolation.md).

The [Python EIP client](python-client.md) provides scoped Session management and the method table. Configure client and daemon timeouts separately.

## Restart after installing a release

Use `a13n-envd daemon` under an external service manager and ask that manager to restart it after installation. Use `a13n-envd supervise` when the deployment has no reliable service manager. The native supervisor replaces an exited daemon at most three times over its lifetime, with one-, two- and four-second backoff. Operator stop drains and cleans up the owned child without replacing it. Stdio remains caller-owned.

After the three crash replacements are exhausted, or an accepted restart fails to confirm the new child's identity after draining the old daemon, the supervisor exits unsuccessfully and its local restart endpoint is unavailable. Inspect the launcher/guest startup log, correct the startup failure, then ask the outer service/container launcher to start a fresh `a13n-envd supervise` process with the same trusted arguments, installation state, credential files and workspace. This creates a new replacement budget; running `a13n-envd restart` cannot revive an exited supervisor. Verify authenticated EIP readiness and open new Sessions before continuing. When the provider cannot restart that target, use its explicit instance-replacement path.

After atomically installing a release at the original executable path, request a version-selected restart from the same account:

```sh
a13n-envd restart --runtime-dir /run/a13n-envd-state --version 0.8.2
```

The runtime directory defaults to `A13N_ENVD_RUNTIME_DIR`. The command verifies the installed version before stopping the old daemon. If the selected version is already running, it succeeds without another restart, so a lost receipt can be retried. An explicit restart does not consume or reset the crash-replacement budget. A restart receipt confirms that the launched child's compiled version matches the requested version; verify authenticated EIP readiness before dispatching work.

The Host owns download, checksum verification and installation. Restart creates a new daemon generation and ends all old Sessions, processes and retained output. Device identity, credentials and workspace files remain. A new Session does not replay commands or recover old handles. Native daemon restart leaves the outer desktop launcher and display running; an external manager's restart follows that manager's service scope.

## Plan output capacity before daemon launch

Size output capacity for concurrent commands, not just the text shown to the user. Standalone daemon defaults are:

| Limit                     | Default |
| ------------------------- | ------- |
| Inline preview            | 2 MiB   |
| Each stdout/stderr stream | 256 MiB |
| Each Session spool        | 1 GiB   |
| Aggregate Device spool    | 4 GiB   |

Each command reserves capacity for stdout and stderr before launch. At these defaults, one live pair reserves 512 MiB. Two pairs can fill the 1 GiB Session spool before additional charges, and retained records leave less room. Reducing the inline preview alone does not reduce the reservation.

If launch returns busy, check output reservations and retained spool charges. Release observations you no longer need, increase the quota, or lower the per-stream limit. Keep `spool >= 2 * per_stream` and `preview <= per_stream`. The daemon can reclaim eligible completed history under pressure; live work keeps its reservation.

Set these limits in the configuration that launches the daemon. Python Local Envd supplies its own preview and file-value defaults through the Host's [runtime fields](../environments/configuration-reference.md#localenvdlaunchconfiguration). Native Docker has separate output limits; Docker with explicitly selected Envd execution uses the daemon's limits.

## Preview, retained bytes, and completion

A command result may contain a preview plus a reference to retained bytes. To collect the output:

1. Keep the returned output reference and its range, completion, and truncation fields.
2. Read bounded pages from that reference, continuing from each returned next offset or cursor. Repeated reads leave retained content available.
3. Check producer completion as well as the retained end. A command can finish before you have read all retained bytes, and a running producer can append more after you reach the current end.
4. Check content completeness separately. Reaching EOF does not recover bytes omitted by a retention limit.
5. Copy important output to application-managed files or durable logs, then release unused process/output observations through their owning EIP methods.

Process inspection reports status; it does not attach output or reset the retention budget. Releasing observations and terminating a live command are separate operations. The private spool and a live connection are temporary execution resources, not durable Agent checkpoints.

## Transfers and uncertain commands

Finish a file transfer with an explicit writer commit and inspect its result. Closing or abandoning the writer aborts incomplete work rather than publishing the file. Keep the operation receipt so you can distinguish non-dispatch, known completion, and an unknown outcome.

After a timeout or transport loss, reconcile the original operation where its replay class permits. A missing acknowledgement does not establish whether the command or write ran. Relaunching it as new intent can repeat the side effect.

Check Session and Device generation before reusing handles. A new generation invalidates old generation-bound handles and output selectors, even if the Provider's remote target still exists. Saved Provider state identifies the target; it cannot reconstruct discarded output or restore every command.

Use the product's Run interrupt to stop a Run, Session close to end its execution scope, and the launcher's cleanup to stop an owned daemon. An abrupt kill leaves cleanup unconfirmed until you obtain evidence of the result.

## Diagnose failures at the right layer

| Symptom                                  | Check                                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- |
| Initialization/Session admission busy    | Device Session capacity, aggregate quotas, pending cleanup, and idle expiry                 |
| Required method absent                   | Required-method selection, effective execution identity, and executable policy              |
| Busy / quota error before command launch | Per-stream pair reservation and retained spool charges                                      |
| Partial preview                          | Returned reference/range and producer/completion evidence                                   |
| Filesystem or command access denied      | Effective execution identity, Sandbox grants, and the [Host's outer boundary](isolation.md) |
| Unknown command/write outcome            | Original operation receipt and replay/reconciliation class; do not retry as new intent      |
| Remote target unavailable                | Provider/backend access and exact saved target identity, not another mount's fallback       |

Check readiness through EIP. For deployment liveness, explicitly enable the separate [health probe](configuration.md#optional-health-probe); it works independently of the EIP transport. The EIP HTTP carrier provides authenticated control and transfer routes, not a general health or browser endpoint. With stdio, write diagnostics away from protocol stdout. Use [configuration](configuration.md) for carrier and bootstrap settings.
