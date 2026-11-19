# Cloudflare Containers Environment gateway

This gateway provides short-lived headless Envd workspaces for a13n Service. Deploy a dedicated Worker per Service trust boundary with a Containers-enabled Cloudflare account and access to its Cloudflare Registry. Use a unique Worker name and either a Workers subdomain or a custom domain. The gateway runs the official `a13n-sandbox` slim image with its standard entrypoint; it does not build a derivative image.

## Deploy from Console

On each Service replica with the Control role, install Node.js, Wrangler **4.164.0**, and regctl **0.11.3**, then configure absolute executable paths (the standard Service image does not include these tools):

```toml
[provisioning.cloudflare]
enabled = true
wrangler = "/usr/local/bin/wrangler"
regctl = "/usr/local/bin/regctl"
```

Configure Service credential encryption, and allow the Control role to reach the Cloudflare API, managed registry, GHCR, and the Worker's `workers.dev` origin. The Service mirrors the pinned official sandbox with registry-to-registry operations; no Docker socket is required. Configure an HTTPS Service public origin if templates will use reverse WebSocket.

In **Settings → Providers → Environment**, choose **Add provider → Cloudflare Containers → Deploy automatically** as a workspace administrator. Use a Cloudflare account with Containers enabled and an existing `workers.dev` subdomain. Enter its Account ID and an account-scoped API token allowed to edit Workers scripts and manage Containers and registry credentials. Service reuses the existing subdomain and creates a dedicated set of resources. When its Envd check is complete, choose **Create template**. Cloudflare bills the account for its usage.

Retire the deployment from the Providers page after deleting its Service Environments and templates. If credentials expire, use the deployment's retry form to replace the API token. Leave provisioning enabled until retirement completes. Keep the generated Worker, namespace, application and registry tag reserved to that deployment; do not replace or mutate them concurrently through the Cloudflare dashboard or another controller. Retirement removes the exact owned image tag without deleting shared image layers or triggering account-wide registry garbage collection.

## Environment templates

The default recipe uses HTTP Envd and public workload egress. For reverse WebSocket without workload Internet, set `{"execution":"envd_ws","network_egress":"disabled"}`. Set an idle-delete policy (for example `config.reclamation.idle_delete_seconds: 1800`) to limit resource usage.

HTTP EIP uses the Worker and Durable Object to reach a private Envd port. Reverse WebSocket uses a dedicated hostname interception to relay only to the configured Service; this works independently of workload Internet access. The Worker retains the Service connection token, not the container. The container-local relay grant is per Environment; do not depend on it to isolate mutually untrusted payloads from Envd inside the same container. Do not place platform credentials in ordinary command defaults.

Stop/resume, persistent workspaces and desktop are outside this gateway's contract. A lost container is never replaced on the same Environment ID. Delete Service environments before decommissioning the gateway; removing the Worker alone does not remove its Container application or registry image.
