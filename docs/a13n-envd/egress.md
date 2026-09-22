# Session egress

Select controlled egress with `A13N_ENVD_EGRESS_MODE=controlled`, `--egress-mode controlled`, or daemon JSON stored outside the workspace:

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "egress": {"mode": "controlled"}
}
```

This enables controlled Sessions on Linux. Launch the daemon as root with native mount/PID/IPC/network namespace permissions. Envd requires Linux 6.1.2 or newer, seccomp, pidfds, mount-tree cloning, `/usr/bin/unshare`, `/usr/sbin/ip`, `/usr/sbin/nft`, `/usr/bin/ldd`, and a system CA bundle. It uses native accounts, not a single-UID user namespace. Without execution UID/GID settings, Sessions retain the root launcher's identity. To run them under another provisioned account, explicitly configure its UID/GID and native sudoers as described in [configuration](configuration.md#native-identity-and-sudo). No account is assumed or created; root execution remains subject to the same egress boundary. Missing privileges or facilities fail startup or Session preparation without fallback.

Controlled mode prepares a private management runtime before accepting work. Every Session uses an isolated worker and must supply an explicit destination policy. Omitting it fails Session creation. Inherit and deny Devices reject Session egress policies. Sandbox grants are independent of egress mode: restricted workers expose only the granted directories and a minimal read-only system view, while disabled Sandbox retains native filesystem access.

## Create a controlled Session

After `initialize`, add `egress` to `session.open.params`:

```json
{
  "expected_device_id": "device-example",
  "expected_generation": 123,
  "protocol_version": "0.1",
  "working_directory": "/workspace",
  "egress": {
    "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
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

| Required `destinations`                           | Behavior                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| `{"mode":"public"}`                               | Permit public DNS names, not direct IP connections.                 |
| `{"mode":"allowlist","hosts":[]}`                 | Deny external access.                                               |
| `{"mode":"allowlist","hosts":["api.github.com"]}` | Permit only exact listed DNS names or explicitly listed public IPs. |

Wildcards, suffix matching and URL patterns are not supported. `inject_hosts` must be nonempty and, in allowlist mode, a subset of `hosts`. Public mode still restricts injection to `inject_hosts`. There is no second daemon-level destination list.

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
    "destinations": {"mode": "allowlist", "hosts": ["api.github.com", "example.com"]},
    "set_secrets": [
      {"env":"GH_TOKEN","value":"<replacement token>","inject_hosts":["api.github.com"]}
    ],
    "remove_secrets": []
  }
}
```

Each successful atomic update increments `revision`. A stale revision returns `conflict`; read current metadata through `environment.describe` or `session.attach` before deciding what to retry. Omitted update fields stay unchanged. `destinations: {"mode":"allowlist","hosts":[]}` denies external access; `destinations: {"mode":"public"}` restores public-domain access. Updates cannot change the Device's Sandbox, identity or network mode.

`set_secrets` adds or replaces bindings. Rotation preserves the sentinel, so an existing process using `$GH_TOKEN` gets the new value on its next HTTPS request. `remove_secrets` deletes bindings; delete then re-add allocates a new sentinel. Old deleted sentinels are rejected in intercepted request headers. When denying a host, remove or change its secret injection bindings in the same update.

New commands receive current bindings. Running processes keep their original environment: adding a variable does not insert it into an existing process, and deleting one does not erase its old sentinel there. Commands cannot override or unset bound secret and CA variables through command environment configuration. Retry fingerprints use the original command request, so policy changes do not accidentally launch a duplicate operation.

## Files, sockets and lifetime

Controlled commands and file operations run in the same native-identity worker. With `sandbox.mode: disabled`, the **original sandbox system tree stays writable**: authorized sudo package installs, service users, permissions, ACLs, linker/CA updates and system-file changes persist across Sessions and are visible from the outer sandbox. `/tmp`, `/run`, device/kernel views and shared memory have private mounts; a workspace under `/tmp` is rebound to its original backing directory. The small immutable management runtime is only for the broker and bootstrap, not a substitute payload image.

Envd hides its configuration, transport credentials, broker runtime and the launching account's `.a13n` directory. Masks are attached before worker views are cloned, so renaming an ancestor does not reveal protected files to a later Session. Protected files with extra hardlinks or workspaces overlapping protected paths reject preparation. Keep unrelated credentials out of the workspace and image; this is not a general filesystem secrecy policy.

With disabled Sandbox, native sudo is enabled by default and uses existing sudoers. Restricted Linux Sandbox instead drops capabilities and blocks privilege gain, including for UID 0. Session-local Unix sockets, socketpairs, PTYs and low-port services work. Namespace/mount reconfiguration, raw/packet/VSOCK networking, network-administration capabilities, unsafe devices/kernel controls and io_uring bypasses are unavailable, including after sudo. These restrictions protect explicit egress and broker credentials rather than making ordinary system administration read-only. The Host still owns CPU/memory limits and the outer sandbox lifecycle.

### Deployment prerequisites

Use a dedicated disposable sandbox without foreign privileged services, cron/systemd jobs that execute payload-writable files, or externally privileged/network-proxy sockets accessible through shared paths. A service outside the worker namespace could otherwise execute modified files or forward a socket request outside the network policy. Do not mount a Docker socket or external SSH-agent/database proxy into the shared tree and assume per-process egress can constrain it.

The launcher must provide trusted artifacts for every daemon start. The private management snapshot protects a running broker from later system-tree changes; it does not make an already modified sandbox image trustworthy for the next startup. Rebuild or restore trusted launcher artifacts as needed. This is not whole-machine flow isolation or isolation between mutually hostile Sessions sharing a writable system tree.

Detach retains the worker and policy during the ordinary Session grace period. Reattach returns current policy metadata. Close, expiry, broker failure and daemon death stop controlled execution and networking. Real secrets and the Session CA private key remain in broker memory.

Each controlled Session reserves its configured Session budgets against Device totals at creation. This conservative allocation can admit fewer idle Sessions than `max_sessions`; increase the corresponding Device totals when needed. Capacity is released after confirmed worker death. If staged workspace files cannot be proven cleaned, their reservation remains charged until daemon teardown; repeated failed cleanup cannot bypass Device accounting.

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
