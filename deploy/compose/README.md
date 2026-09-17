# Single-host Service with native Docker

`a13n-service-single-host.yaml` runs Service (including its Worker), PostgreSQL, and Redis. The non-root Service process uses the host Docker Engine through `/var/run/docker.sock`. Access to this socket grants host Docker authority, so deploy only where Service is trusted with that authority. The deployment owns and automatically registers its Docker and Direct Local Organization Providers.

Build development images with `make image-a13n-service image-docker-environment`. Find the socket's numeric group ID **as mounted inside a container** and start the stack from the repository root:

```sh
umask 077
(set -C; python3 -c 'import base64,secrets; print("A13N_SERVICE_SECRET_MASTER_KEY_BASE64=" + base64.b64encode(secrets.token_bytes(32)).decode())' > .env.single-host)
export A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL=admin@example.com
export A13N_SERVICE_IMAGE=a13n-service:local
export A13N_DOCKER_SOCKET_GID="$(docker run --rm --entrypoint stat -v /var/run/docker.sock:/var/run/docker.sock "$A13N_SERVICE_IMAGE" -c %g /var/run/docker.sock)"
docker compose --env-file .env.single-host -f deploy/compose/a13n-service-single-host.yaml up -d
```

The mounted socket group can differ from the host value on Docker Desktop; use the value observed inside the container. The API listens on localhost port 8000. The included database password is for this local stack; configure credentials and authentication for other deployments. The mounted TOML is the shared deployment configuration.

A locally built image such as `my-env:dev` is immediately available to templates using the same host Engine. Missing images are pulled when first needed. The default native Docker image uses the `dev` tag published from `main`; pin a digest for reproducibility. Rebuilding or pulling a tag does not recreate existing Environment containers. Delete the old Environment and create another to use a new image version.

`service-data` preserves Service files and managed Direct Local directories. Use `/app/var` as a Direct Local template base; concrete roots become `/app/var/environments/<env_id>`. Ordinary `stop`/`up` and Service recreation retain the volume. Removing deployment volumes destroys their data.

Docker templates have a private `/workspace` and may bind explicitly approved external host paths. Template sources resolve in the host Engine filesystem namespace. Source directories must already exist, with permissions suitable for the selected container user. Environment deletion preserves these external paths.

`single_host` allows multiple Worker processes only when all share the required files, Engine, and lifecycle database. It does not discover physical topology. Use `distributed` for Workers deployed independently across machines; that mode rejects Direct Local and Docker and permits E2B and supported remote Providers. The Envd sandbox image remains available for independently operated Envd deployments.
