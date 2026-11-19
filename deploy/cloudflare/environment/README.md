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

## Attach an existing gateway

To operate the resources yourself, follow the manual deployment below, and choose **Use existing gateway** in Console. Enter the gateway HTTPS URL and `GATEWAY_TOKEN`; the Cloudflare account token stays with your deployment tooling. Copy the gateway files with links dereferenced when deploying outside this checkout (`cp -L worker.mjs wrangler.jsonc <deployment-directory>`).

1. Mirror the `linux/amd64` variant of an official `ghcr.io/converge-ai-labs/a13n-sandbox:<VERSION>-slim` image containing `A13N_ENVD_HTTP_BOOTSTRAP_TOKEN` support to your Cloudflare Registry. The `durable_object` scheduling policy requires a Cloudflare Registry digest. Replace the account and digest placeholders in your deployment copy of `wrangler.jsonc` with that reference. For development before such a release, build `deploy/docker/images/sandbox/Dockerfile` with `SANDBOX_VARIANT=slim` from the repository root and push it to the same registry.
2. Deploy from this directory with `npx wrangler@4.164.0 deploy --secrets-file /path/to/private-gateway-secrets.json`. Supply a randomly generated `GATEWAY_TOKEN` as a Worker secret. For reverse WebSocket, add `SERVICE_ORIGIN` as a Worker variable containing the exact HTTPS origin from Service's `server.public_url`. The Service origin must accept WebSocket upgrades.
3. Create a Service Environment Provider of type `cloudflare`, with `config.endpoint` set to the gateway HTTPS origin and credential `api_key` set to the gateway token. Keep Cloudflare deployment credentials out of Service. Treat the gateway token as operator-level access to every Environment on that gateway.
4. Create a template for that Provider. The default `config.recipe` selects `envd_http` and public workload egress. To select reverse WebSocket without workload Internet, use `{"execution":"envd_ws","network_egress":"disabled"}`. Set an idle-delete policy (for example `config.reclamation.idle_delete_seconds: 1800`) to limit resource usage.

HTTP EIP uses the Worker and Durable Object to reach a private Envd port. Reverse WebSocket uses a dedicated hostname interception to relay only to the configured Service; this works independently of workload Internet access. The Worker retains the Service connection token, not the container. The container-local relay grant is per Environment; do not depend on it to isolate mutually untrusted payloads from Envd inside the same container. Do not place platform credentials in ordinary command defaults.

Stop/resume, persistent workspaces and desktop are outside this gateway's contract. A lost container is never replaced on the same Environment ID. Delete Service environments before decommissioning the gateway; removing the Worker alone does not remove its Container application or registry image.
