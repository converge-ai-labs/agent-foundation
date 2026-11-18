# Cloudflare Containers Environment gateway

This gateway provides short-lived headless Envd workspaces for a13n Service. Deploy a dedicated Worker per Service trust boundary with a Containers-enabled Cloudflare account and Docker. Use a unique Worker name and either a Workers subdomain or a custom domain. The packaged image pins the slim Envd release.

1. Deploy from this directory with `npx wrangler@4.164.0 deploy --secrets-file /path/to/private-gateway-secrets.json`. Supply a randomly generated `GATEWAY_TOKEN` as a Worker secret. For reverse WebSocket, add `SERVICE_ORIGIN` as a Worker variable containing the exact HTTPS origin from Service's `server.public_url`. The Service origin must accept WebSocket upgrades.
2. Create a Service Environment Provider of type `cloudflare`, with `config.endpoint` set to the gateway HTTPS origin and credential `api_key` set to the gateway token. Keep Cloudflare deployment credentials out of Service. Treat the gateway token as operator-level access to every Environment on that gateway.
3. Create a template for that Provider. The default `config.recipe` selects `envd_http` and public workload egress. To select reverse WebSocket without workload Internet, use `{"execution":"envd_ws","network_egress":"disabled"}`. Set an idle-delete policy (for example `config.reclamation.idle_delete_seconds: 1800`) to limit resource usage.

HTTP EIP uses the Worker and Durable Object to reach a private Envd port. Reverse WebSocket uses a dedicated hostname interception to relay only to the configured Service; this works independently of workload Internet access. The Worker retains the Service connection token, not the container. The container-local relay grant is per Environment; do not depend on it to isolate mutually untrusted payloads from Envd inside the same container. Do not place platform credentials in ordinary command defaults.

Stop/resume, persistent workspaces and desktop are outside this gateway's contract. A lost container is never replaced on the same Environment ID. Delete Service environments before decommissioning the gateway; removing the Worker alone does not remove its Container application or registry image.
