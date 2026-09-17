# Single-host Service with native Docker

`a13n-service-single-host.yaml` runs Service (including its Worker), PostgreSQL, Redis, and a dedicated Docker-in-Docker Engine. Service runs as UID/GID 10001 without privilege. Only DinD is privileged. The shared socket belongs to DinD; the host Docker socket is never mounted.

Build development images with `make image-a13n-service image-docker-environment`. Choose an existing absolute host directory for external mounts, then start this stack from the repository root:

```sh
# Create this once, keep it private, and retain it across deployment restarts.
umask 077
(set -C; python3 -c 'import base64,secrets; print("A13N_SERVICE_SECRET_MASTER_KEY_BASE64=" + base64.b64encode(secrets.token_bytes(32)).decode())' > .env.single-host)
export A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL=admin@example.com
export A13N_SERVICE_IMAGE=a13n-service:local
export A13N_DOCKER_HOST_DATA=/absolute/path/to/approved-data
docker compose --env-file .env.single-host -f deploy/compose/a13n-service-single-host.yaml up -d
```

The API listens on localhost port 8000. The included database password is for this local stack; configure credentials and authentication for other deployments. The mounted TOML is the shared deployment configuration. Local Providers are published by deployment configuration rather than created through the Provider editor.

DinD has its own image store. Load a locally built execution image into that Engine before selecting `a13n-docker-environment:local` with pull policy `never` in a template:

```sh
docker save a13n-docker-environment:local | docker compose --env-file .env.single-host -f deploy/compose/a13n-service-single-host.yaml exec -T docker docker load
```

Alternatively, select a published image and let DinD pull it. The default native Docker image uses the `dev` tag published from `main`; pin an image digest when deployment reproducibility is required. Remote registry credentials must be available to that Engine's client as appropriate to the deployment.

The `docker-data` volume preserves containers across DinD restarts. `service-data` preserves Service files and managed Direct Local directories. Use `/app/var` as a Direct Local template base, for example; concrete roots become `/app/var/environments/<env_id>`. Ordinary `stop`/`up` and recreation of Service retain these volumes. Removing the deployment's volumes destroys their data.

Docker templates have a private `/workspace`. They may also bind one or more explicitly approved external host paths, for example source `/absolute/path/to/approved-data/reference` to target `/reference` read-only. Compose exposes the approved base to DinD at the same absolute path, so template source paths resolve correctly. Add another explicit bind in Compose to approve another base; no change is needed per Environment. Source directories must already exist, with permissions suitable for the selected container user. Environment deletion preserves these external paths.

`single_host` allows multiple Worker processes only when all share the required files, Engine, and lifecycle database. It does not discover or prove physical topology. Use `distributed` for Workers deployed independently across machines; that mode rejects Direct Local and Docker and still permits E2B and supported remote Providers. Privileged DinD requires a compatible Linux container host and appropriate operator trust; it is not a strong boundary against a privileged compromise.

The Envd sandbox image remains available for independently operated Envd deployments. Native Docker uses the separate Envd-free image.
