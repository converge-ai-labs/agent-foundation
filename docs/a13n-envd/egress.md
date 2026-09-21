# Session egress

Enable egress in the daemon's JSON configuration, stored outside the workspace:

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "egress": {"enabled": true}
}
```

This enables controlled Sessions on Linux. Ordinary Sessions retain their existing behavior. Envd requires Linux 6.1.2 or newer, working user/network/mount/PID namespaces, seccomp, pidfds, recursive read-only mounts, `/usr/bin/unshare`, `/usr/sbin/ip`, `/usr/sbin/nft`, and a system CA bundle. Missing permissions or facilities reject controlled Session creation. Some sandbox images require launching envd with the image's privileged account; test the actual image and launch identity.

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

No `HTTP_PROXY` or `HTTPS_PROXY` setting is needed. Kernel routing sends traffic through the outer broker. For HTTPS on port 443, it replaces sentinels in request **header values** only, and only for that secret's `inject_hosts`. URLs and request bodies are unchanged. The broker validates upstream TLS. Standard CA bundles and `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `NODE_EXTRA_CA_CERTS` establish trust inside the Session; clients with certificate pinning or a separate private trust store need explicit integration.

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

Controlled commands and file operations run in the same isolated worker. The workspace, private `/tmp`, runtime storage and shared-memory directory are writable; the remaining image is read-only. Envd hides its configuration, transport credentials, private runtime and the launching account's `.a13n` directory. Protected files with extra hardlinks or workspaces overlapping protected paths reject creation. Keep unrelated credentials out of the workspace and image; this is not a general filesystem secrecy policy.

Controlled payloads cannot connect named Unix sockets, including Docker sockets, SSH agents and local database sockets. Anonymous stream/SEQPACKET socketpairs remain available for process communication. TCP/UDP sockets work through the network policy. Capabilities are dropped and privilege gains disabled. The Host still owns CPU/memory limits and the outer sandbox lifecycle.

Detach retains the worker and policy during the ordinary Session grace period. Reattach returns current policy metadata. Close, expiry, broker failure and daemon death stop controlled execution and networking. Real secrets and the Session CA private key remain in broker memory.

Each controlled Session reserves its configured Session budgets against Device totals at creation. This conservative allocation can admit fewer idle Sessions than `max_sessions`; increase the corresponding Device totals when needed. Capacity is released after confirmed worker death. If staged workspace files cannot be proven cleaned, their reservation remains charged until daemon teardown; repeated failed cleanup cannot bypass Device accounting.

## Verify a sandbox image

Install the prerequisites above and run the public EIP integration test with the same account that will launch envd:

```bash
python3 crates/a13n-envd/tests/egress_linux.py /path/to/a13n-envd
```

The test uses generated dummy credentials against `httpbin.org`. `--http-host httpbingo.org` selects an alternative compatible endpoint. On images that only permit essential development services, `--http-host api.github.com` verifies header substitution through GitHub media-type validation and response-header redaction instead of Basic authentication and body echo. It checks real HTTPS authentication, response redaction, policy updates, replay, filesystem access, socket restrictions, capacity reuse, and broker-death cleanup. `--local-network-fixture` also creates a temporary public-address alias in the outer test namespace for TCP/UDP echo checks; use it only in a disposable privileged Linux sandbox.
