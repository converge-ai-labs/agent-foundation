---
title: Historical cloud egress validation
sidebarTitle: Historical egress validation
description: Scope and limits of the September 2026 cloud probes, not a current Provider support matrix.
---

This record preserves the evidence from an earlier test campaign. Use [current egress prerequisites and verification](egress.md#verify-a-sandbox-image) before admitting work on a new deployment.

## Cloud platform validation record

This historical record covers probes completed by **2026-09-22** using one Linux x86_64 binary on five cloud platforms. Tests used generated credentials; provider credentials stayed outside Sessions.

**The matrix predates the native identity and sudo changes introduced on 2026-09-22 and has not been rerun for that implementation.** Use the [current root-launcher requirements](egress.md) for new deployments. Old non-root launches, read-only system-tree checks and named-socket denials describe the tested binary. Current behavior is explained in [files, sockets and lifetime](egress.md#files-sockets-and-lifetime). The record covers these Envd sandbox probes, not Provider/Service integration or platform-wide support.

The retained record does not identify a full source revision or image digest, so it cannot reproduce the tested binary by itself. Record the revision, binary checksum, image digest, and runtime details together when repeating the campaign.

### Cloud results for the tested binary

| Platform / runtime   | Observed kernel          | Tested Envd launch identity | Result and coverage                                                                                                         |
| -------------------- | ------------------------ | --------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| E2B                  | `6.1.158+`               | Default account, UID 1000   | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Runloop              | `6.18.32`                | Default account, UID 1000   | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Vercel Sandbox       | `6.18.49`                | Root through `sudo`         | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Daytona              | `6.8.0-138-generic`      | Root through `sudo`         | GitHub header fixture and controlled Session lifecycle passed; external echo sites were blocked.                            |
| Modal default gVisor | Reported `4.19.0-gvisor` | Root                        | Ordinary Session/readiness, shell execution and file read/write passed. Controlled Session creation returned `unsupported`. |
| Modal VM Sandbox     | `7.2.6`                  | Root                        | Ordinary Envd flow, full HTTPS fixture, controlled Session lifecycle and in-VM TCP/UDP echo passed.                         |

The **full HTTPS fixture** checked substituted Basic Auth headers, exact secret redaction from echo responses and secret-free Session metadata. The **controlled Session lifecycle** checked policy updates, replay, that binary's filesystem/socket restrictions, quota reuse and cleanup of detached `setsid` descendants after broker death. Both Modal runtimes also ran ordinary Session shell/file smoke checks; the other platforms ran the controlled fixture.

TCP/UDP echo passed with `--local-network-fixture` in a privileged local Linux container and Modal VM. These were echo services inside the outer test environment. External Internet TCP/UDP and TCP/UDP on E2B, Runloop, Vercel or Daytona were not tested. Sprites and unlisted platforms were outside this campaign.

### Daytona network restrictions

Direct requests outside Envd reached `api.github.com` but failed for `httpbin.org`, `httpbingo.org` and `example.com`. An Envd allowlist cannot widen the outer platform's permitted destinations.

The GitHub fixture received HTTP 415 for an unsupported `Accept` value. Replacing its sentinel with `application/json` produced HTTP 200, then redacted that value from response `Content-Type`. This verified header substitution and header redaction; Basic Auth/body echo remained blocked.

### Modal runtime selection

The tested default gVisor runtime rejected controlled `session.open` with `unsupported` and `unshare: Operation not permitted`; a separate `nft list ruleset` returned `Protocol not supported`. Root launch did not provide the missing facilities.

The tested VM Sandbox runtime passed with this Modal SDK selection:

```python
sandbox = modal.Sandbox.create(
    app=app,
    image=image,
    experimental_options={"vm_runtime": True},
)
```

The SDK setting selected the outer runtime. It was neither an Envd field nor a new built-in Modal Provider option. For a new image or binary, prepare the current prerequisites and rerun [image verification](egress.md#verify-a-sandbox-image).

### Test preparation and limits

The probes installed missing networking tools and CA certificates. Vercel used an account-accepted timeout; Daytona omitted resource overrides incompatible with its default snapshot. Modal's host-side Python client used proxy-support dependencies and an explicit CA bundle. These were test setup changes; production Provider code was unchanged.

All temporary sandboxes were terminated. Native Provider instances were reconciled absent, and all five Modal instances, including diagnostic attempts, were confirmed stopped. Record the date, image, runtime, kernel, launch identity and fixture coverage together when repeating the campaign, including changed behavior still awaiting a test.
