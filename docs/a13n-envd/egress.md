# Session egress

Enable egress with `A13N_ENVD_EGRESS_ENABLED=true`, `--egress-enabled true`, or daemon JSON stored outside the workspace:

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "egress": {"enabled": true}
}
```

This enables controlled Sessions on Linux. Launch the daemon as root with native mount/PID/IPC/network namespace permissions. Envd requires Linux 6.1.2 or newer, seccomp, pidfds, mount-tree cloning, `/usr/bin/unshare`, `/usr/sbin/ip`, `/usr/sbin/nft`, `/usr/bin/ldd`, and a system CA bundle. It uses native accounts, not a single-UID user namespace. Without execution UID/GID settings, Sessions retain the root launcher's identity. To run them under another provisioned account, explicitly configure its UID/GID and native sudoers as described in [configuration](configuration.md#native-identity-and-sudo). No account is assumed or created; root execution remains subject to the same egress boundary. Missing privileges or facilities fail startup or Session preparation without fallback.

The [sandbox image](sandbox.md) includes the userspace prerequisites and a root launcher, but leaves egress disabled by default. Enabling it still requires outer-runtime permissions and an explicit Session policy; the image does not auto-elevate or bypass the platform's restrictions.

Enabling egress prepares a private management runtime before accepting work. All Sessions on that daemon use a worker to protect the broker. Only Sessions with an explicit EIP egress policy receive a private network and destination enforcement; omitting the policy does not silently enable filtering.

## Create a controlled Session

After `initialize`, add `egress` to `session.open.params`:

```json
{
  "expected_device_id": "device-example",
  "expected_generation": 123,
  "protocol_version": "0.1",
  "working_directory": "/workspace",
  "egress": {
    "allow_hosts": ["api.github.com"],
    "secrets": [
      {
        "env": "GH_TOKEN",
        "value": "<real token supplied by the trusted caller>",
        "inject_hosts": ["api.github.com"]
      }
    ]
  }
}
```

The returned descriptor includes a revision and secret metadata, for example `{"env":"GH_TOKEN","sentinel":"a13n_7d4e9c2a_GH_TOKEN","inject_hosts":["api.github.com"]}`. It never returns `value`. Supply real values over the authenticated EIP carrier, not daemon configuration or command arguments. Avoid logging request payloads.

Commands receive the sentinel in `GH_TOKEN`:

```bash
curl https://api.github.com/user -H "Authorization: Bearer $GH_TOKEN"
```

No `HTTP_PROXY` or `HTTPS_PROXY` setting is needed. Kernel routing sends traffic through the outer broker. For HTTPS on port 443, it replaces sentinels in request **header values** only, and only for that secret's `inject_hosts`. URLs and request bodies are unchanged. The broker validates upstream TLS. A private bundle combining system trust and the Session CA is supplied through `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `NODE_EXTRA_CA_CERTS`. Clients with certificate pinning or a separate trust store need explicit integration. Envd does not bind over the native system CA bundle, so `update-ca-certificates` keeps working.

Native sudo normally strips these environment variables. For root HTTPS commands, preserve the required variables according to your existing sudoers policy, for example:

```bash
sudo --preserve-env=SSL_CERT_FILE,CURL_CA_BUNDLE curl https://api.github.com/
```

Alternatively configure the application's trust store. Envd does not silently edit sudoers or the global trust store. Destination enforcement remains active even when a client's TLS trust is misconfigured.

Response header values, body bytes and trailers redact exact secret values, including matches across chunks. This is literal redaction, not prevention of an upstream service intentionally encoding or transforming a secret. HTTP uses HTTP/1.1; protocol upgrades and compressed responses are rejected. Request compression is not rewritten. HTTP on port 80 performs no credential injection. Other TCP ports and UDP forward permitted traffic without secret substitution.

## Destination policy

| `allow_hosts` | Behavior                                                                    |
| ------------- | --------------------------------------------------------------------------- |
| Omitted       | Permit public DNS names. Direct IP connections still need explicit entries. |
| `[]`          | Deny external access.                                                       |
| Nonempty      | Permit only these exact DNS names or explicit public IP entries.            |

Wildcards, suffix matching and URL patterns are not supported. `inject_hosts` must be nonempty and, for a restricted allowlist, a subset of `allow_hosts`. Without an allowlist, injection is still restricted to `inject_hosts`.

The broker rejects private, loopback, link-local, metadata and other nonpublic upstream addresses, including DNS answers containing any nonpublic address. Direct IP access never grants credential injection. The current interception path uses IPv4 inside the Session; DNS names may resolve to IPv4 or IPv6 upstream. Native IPv6 connections from payloads have no external route.

Local services in the Session's own loopback namespace remain reachable. They do not refer to the outer sandbox's loopback services.

## Update without restarting

Send `egress.update` with the Session selector and its current revision:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "eip_session": "session-example",
  "method": "egress.update",
  "params": {
    "expected_revision": 1,
    "allow_hosts": ["api.github.com", "example.com"],
    "set_secrets": [
      {"env":"GH_TOKEN","value":"<replacement token>","inject_hosts":["api.github.com"]}
    ],
    "remove_secrets": []
  }
}
```

Each successful atomic update increments `revision`. A stale revision returns `conflict`; read current metadata through `environment.describe` or `session.attach` before deciding what to retry. Omitted update fields stay unchanged. `allow_hosts: []` denies external access; `unrestricted: true` restores public-domain access and cannot accompany `allow_hosts`.

`set_secrets` adds or replaces bindings. Rotation preserves the sentinel, so an existing process using `$GH_TOKEN` gets the new value on its next HTTPS request. `remove_secrets` deletes bindings; delete then re-add allocates a new sentinel. Old deleted sentinels are rejected in intercepted request headers. When denying a host, remove or change its secret injection bindings in the same update.

New commands receive current bindings. Running processes keep their original environment: adding a variable does not insert it into an existing process, and deleting one does not erase its old sentinel there. Commands cannot override or unset bound secret and CA variables through command environment configuration. Retry fingerprints use the original command request, so policy changes do not accidentally launch a duplicate operation.

## Files, sockets and lifetime

Controlled commands and file operations run in the same native-identity worker. The **original sandbox system tree stays writable**: authorized sudo package installs, service users, permissions, ACLs, linker/CA updates and system-file changes persist across Sessions and are visible from the outer sandbox. `/tmp`, `/run`, device/kernel views and shared memory have private mounts; a workspace under `/tmp` is rebound to its original backing directory. The small immutable management runtime is only for the broker and bootstrap, not a substitute payload image.

Envd hides its configuration, transport credentials, broker runtime and the launching account's `.a13n` directory. Masks are attached before worker views are cloned, so renaming an ancestor does not reveal protected files to a later Session. Protected files with extra hardlinks or workspaces overlapping protected paths reject preparation. Keep unrelated credentials out of the workspace and image; this is not a general filesystem secrecy policy.

Native sudo is enabled by default and uses existing sudoers. Session-local Unix sockets, socketpairs, PTYs and low-port services work. Namespace/mount reconfiguration, raw/packet/VSOCK networking, network-administration capabilities, unsafe devices/kernel controls and io_uring bypasses are unavailable, including after sudo. These restrictions protect explicit egress and broker credentials rather than making ordinary system administration read-only. The Host still owns CPU/memory limits and the outer sandbox lifecycle.

### Deployment prerequisites

Use a dedicated disposable sandbox without foreign privileged services, cron/systemd jobs that execute payload-writable files, or externally privileged/network-proxy sockets accessible through shared paths. A service outside the worker namespace could otherwise execute modified files or forward a socket request outside the network policy. Do not mount a Docker socket or external SSH-agent/database proxy into the shared tree and assume per-process egress can constrain it.

The launcher must provide trusted artifacts for every daemon start. The private management snapshot protects a running broker from later system-tree changes; it does not make an already modified sandbox image trustworthy for the next startup. Rebuild or restore trusted launcher artifacts as needed. This is not whole-machine flow isolation or isolation between mutually hostile Sessions sharing a writable system tree.

Detach retains the worker and policy during the ordinary Session grace period. Reattach returns current policy metadata. Close, expiry, broker failure and daemon death stop controlled execution and networking. Real secrets and the Session CA private key remain in broker memory.

Each controlled Session reserves its configured Session budgets against Device totals at creation. This conservative allocation can admit fewer idle Sessions than `max_sessions`; increase the corresponding Device totals when needed. Capacity is released after confirmed worker death. If staged workspace files cannot be proven cleaned, their reservation remains charged until daemon teardown; repeated failed cleanup cannot bypass Device accounting.

## Provider validation record

The following results were recorded by **2026-09-22**. All five cloud platforms used the same Linux x86_64 binary and generated dummy credentials. Provider credentials stayed outside the Session. These are envd runtime probes on disposable sandboxes, not validation of a Provider/Service egress integration or every vendor image.

**These results predate the native identity and sudo changes introduced on 2026-09-22.** Current egress requires a root launcher, native accounts and the prerequisites at the top of this page. The old non-root launch results below do not establish current non-root launcher support. Read-only system-tree and named-Unix-socket denial checks belonged to the old implementation; current writable-system and native-socket behavior is described in [files, sockets and lifetime](#files-sockets-and-lifetime). The cloud matrix has not been rerun against that implementation.

### Cloud results for the tested binary

| Platform / runtime   | Observed kernel          | Tested envd launch identity | Result and coverage                                                                                                         |
| -------------------- | ------------------------ | --------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| E2B                  | `6.1.158+`               | Default account, UID 1000   | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Runloop              | `6.18.32`                | Default account, UID 1000   | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Vercel Sandbox       | `6.18.49`                | Root through `sudo`         | Full HTTPS fixture and controlled Session lifecycle passed.                                                                 |
| Daytona              | `6.8.0-138-generic`      | Root through `sudo`         | GitHub header fixture and controlled Session lifecycle passed; external echo sites were blocked.                            |
| Modal default gVisor | Reported `4.19.0-gvisor` | Root                        | Ordinary Session/readiness, shell execution and file read/write passed. Controlled Session creation returned `unsupported`. |
| Modal VM Sandbox     | `7.2.6`                  | Root                        | Ordinary envd flow, full HTTPS fixture, controlled Session lifecycle and in-VM TCP/UDP echo passed.                         |

The **full HTTPS fixture** verifies Basic Auth using a substituted header, exact secret redaction from the echo response, and absence of the real secret from Session metadata. The **controlled Session lifecycle** checks policy hot updates, replay, the tested version's filesystem/socket restrictions, quota reuse and cleanup of a detached `setsid` descendant after broker death. The probes exercise the public EIP interface. Separate ordinary Session shell/file smoke checks were run on both Modal runtimes; the other cloud runs used the controlled Session fixture.

TCP/UDP echo passed in a disposable privileged local Linux container and in Modal VM using `--local-network-fixture`. The echo services ran inside the outer test environment; these results do not claim external Internet TCP/UDP interoperability. TCP/UDP echo was not tested on E2B, Runloop, Vercel or Daytona. Sprites and other unlisted platforms were not covered by this validation campaign.

### Daytona network restrictions

Direct requests outside envd to `httpbin.org`, `httpbingo.org` and `example.com` failed, while `api.github.com` remained reachable. This is consistent with Daytona's [organization-tier network restrictions](https://www.daytona.io/docs/en/network-limits/); the tested organization's tier was not independently verified and its network policy was not changed. An envd allowlist cannot override an outer platform restriction.

The `--http-host api.github.com` fixture first receives HTTP 415 for an unsupported `Accept` value. Substituting the sentinel with `application/json` produces HTTP 200, and the same value is redacted from the response `Content-Type`. This proves header substitution and response-header redaction, not the blocked Basic Auth/body-echo scenario.

### Modal runtime selection

Default gVisor rejected the tested controlled `session.open` with `unsupported` and `unshare: Operation not permitted`. A separate `nft list ruleset` probe returned `Protocol not supported`. Running as root did not supply the required facilities.

The [VM Sandbox runtime (Beta)](https://modal.com/docs/guide/vm-sandboxes) passed the test. Select it when creating the sandbox through the Modal SDK:

```python
sandbox = modal.Sandbox.create(
    app=app,
    image=image,
    experimental_options={"vm_runtime": True},
)
```

This SDK setting selects the outer runtime; it is not an envd configuration field or an option added to the built-in Modal Provider by this validation. Provision the current envd prerequisites and rerun the tests below before deploying a new binary or image.

### Test preparation and limits

Test images were prepared with missing networking tools and CA certificates. Temporary launch probes used a Vercel timeout accepted by the account and omitted Daytona resource overrides incompatible with its default snapshot. Modal's local SDK client needed proxy-support dependencies and an explicit CA bundle path for the host Python installation; these were client connection settings, not payload proxy configuration. None of these probe adjustments changed production Provider code.

All temporary cloud sandboxes were terminated; native-provider instances were reconciled absent and all five Modal instances, including connection-diagnostic attempts, were confirmed stopped. For a new validation run, record the validation date, image, runtime, kernel, launch identity and fixture coverage together, including any behavior changes that have not been retested. A pass on one image or a previous envd binary is not a platform-wide support guarantee.

## Verify a sandbox image

Install the prerequisites above, provision the execution account, and run the public EIP integration test with the same root launcher used for deployment:

```bash
python3 crates/a13n-envd/tests/egress_linux.py /path/to/a13n-envd

# Additional native sudo, package, identity, file and cleanup checks.
# Requires a disposable Debian/Ubuntu sandbox, sudo, ACL tools and UID/GID 1000
# with a test-only NOPASSWD sudoers rule. This test changes the sandbox system.
python3 crates/a13n-envd/tests/execution_linux.py /path/to/a13n-envd --controlled
```

The test uses generated dummy credentials against `httpbin.org`. `--http-host httpbingo.org` selects an alternative compatible endpoint. On images that only permit essential development services, `--http-host api.github.com` verifies header substitution through GitHub media-type validation and response-header redaction instead of Basic authentication and body echo. It checks real HTTPS authentication, response redaction, policy updates, replay, filesystem access, native Unix sockets, capacity reuse, and broker-death cleanup. `--local-network-fixture` also creates a temporary public-address alias in the outer test namespace for TCP/UDP echo checks; use it only in a disposable privileged Linux sandbox.
