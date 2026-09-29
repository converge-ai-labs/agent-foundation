# Service with Docker Compose

## Local quickstart

`a13n-service-quickstart.yaml` is a standalone local trial: Service and Console, PostgreSQL, Redis, and a one-shot initializer. It publishes only `127.0.0.1:8080`, runs the Service as the image's non-root user, and does not mount the host Docker socket.

Download the file into an empty directory and run:

```sh
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

From a checkout, use `-f deploy/docker/compose/a13n-service-quickstart.yaml` instead. The source file uses the published `dev` image; a Service release's copy pins that release's image. To test a local build, run `make image-a13n-service` and prefix the Compose command with `A13N_SERVICE_IMAGE=a13n-service:local`.

Open <http://127.0.0.1:8080> and sign in with **`admin@example.com` / `local-public-password-123`**. **Do not expose this trial stack to other machines:** those credentials are public. Add your own model provider under **Models → Add model**, create an agent and select **Try agent**. See [Get started](../../../docs/a13n-service/get-started.md) for the full flow.

The initializer migrates the schema and creates the first organization, workspace and administrator. Repeated starts preserve accounts and data; an initialization failure blocks Service startup. Inspect `docker compose -f a13n-service-quickstart.yaml logs init service` if startup fails.

- **Stop and retain data:** `docker compose -f a13n-service-quickstart.yaml down`.
- **Resume:** repeat `up -d --wait` with the same file and directory.
- **Use another port:** prefix the start command with `A13N_PORT=8081` and open <http://127.0.0.1:8081>.
- **Delete all trial data:** `docker compose -f a13n-service-quickstart.yaml down --volumes`. The next start creates the public trial account again.

The quickstart has its own Compose project and volumes, separate from the deployment below. Keep using the same file/project to resume your data. Plain chat needs no execution environment. Hosted sandbox providers and external Envd targets can be configured separately; local Docker environments require the explicit host authority described below.

## Single-host deployment with native Docker

`a13n-service.yaml` runs the Service with every role in one process (`run --role all`), PostgreSQL and Redis. Only the Service is published, on `127.0.0.1:8080`. It serves the Console and the API from one origin, which browsers, API clients and SDKs share. The Service migrates its schema when it starts. It uses the host Docker Engine through `/var/run/docker.sock`: the container starts as root only to join the group that owns the socket, then runs as the image's non-root `app` user. Access to this socket grants host Docker authority, so deploy only where the Service is trusted with that authority.

From the repository root, start the stack and print its Console URL after the Service becomes healthy:

```sh
make compose-up
```

The source Compose file defaults to the published `ghcr.io/converge-ai-labs/a13n-service:latest` image, so no source build is needed. The equivalent direct command is `docker compose -f deploy/docker/compose/a13n-service.yaml up -d --wait`; Compose does not print the Console URL after a detached start. To use a local build instead, run `make image-a13n-service` and then `A13N_SERVICE_IMAGE=a13n-service:local make compose-up`.

Without a checkout, download `a13n-service.yaml` from a Service release and run `docker compose -f a13n-service.yaml up -d --wait` in its directory. The release's copy pins that release's image, for `linux/amd64` or `linux/arm64`.

Open <http://127.0.0.1:8080> and create the first administrator with an email and a password of at least 12 characters; this signs you in. Only the first visitor can do this, and only the host itself reaches the published port. To create the administrator without a browser, run the `bootstrap` command instead, which prompts for the password:

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml exec service \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

CLI bootstrap is completed by the next Service startup; if you run it inside an already running stack, restart `service` to prepare workspace defaults. The browser bootstrap prepares them before returning.

`A13N_PORT` publishes another port and moves the public URL with it; `make compose-up` prints the mapped address. `A13N_SERVICE_IMAGE` selects another image tag. Pull the Service image before starting when you want to refresh a cached `latest` tag. <http://127.0.0.1:8080/readyz> reports Service readiness at the default port.

`make compose-smoke` exercises both Compose stacks on disposable projects, including initialization, sign-in and credential persistence across restarts.

## Configuration

The Service's `environment` in the Compose file sets its settings as `A13N_<SECTION>__<FIELD>` variables over the image's defaults: the public URL, PostgreSQL, Redis and the encryption key file. Add other settings the same way, with list values as JSON; see the [configuration reference](../../../docs/a13n-service/configuration-reference.md) for every setting. The included database password protects only this unpublished local database. The Service reads its configuration at startup: after an edit, run `up -d --wait` again, which recreates the Service.

Outbound provider requests reject private addresses and plain HTTP by default. To use, for example, a model server on the Docker host, allow it explicitly in the Service's `environment`:

```yaml
A13N_PROVIDERS__PRIVATE_DOMAINS: '["host.docker.internal"]'
A13N_PROVIDERS__HTTP_ORIGINS: '["http://host.docker.internal:11434"]'
```

## Docker environments

The stack explicitly enables Docker provisioning. When the mounted Engine answers, each new workspace automatically receives a **Docker** provider and **Linux Sandbox** template. Existing workspaces are initialized once at Service startup. Failures get two bounded retries and are logged; restart after fixing Engine access to try again. User edits, disabling and deletion of successfully initialized defaults survive restarts.

The default template pins the published `ghcr.io/converge-ai-labs/a13n-docker-environment` image at the installed Service version, with `pull_policy: if_missing`. Workspace initialization does not pull it; the Engine pulls it when an instance is created if needed. The image includes Python 3.13, uv, Node.js 24, pnpm and common command-line and C/C++ build tools. `A13N_DOCKER_ENVIRONMENT_IMAGE` overrides the image for newly initialized workspaces. To use a local build, run `make image-docker-environment` and start Compose with `A13N_DOCKER_ENVIRONMENT_IMAGE=a13n-docker-environment:local`; set an existing template's pull policy to `never` if it must use only local images. Edit existing templates through Console or the API.

For a registry image, set `provisioning.docker.image` to its reference and `pull_policy` to `if_missing`, using the JSON `A13N_PROVISIONING__DOCKER` override. Manually created Docker templates retain the [release-matched image defaults](../../../docs/a13n-service/environments.md#docker-image-versions) and `if_missing` policy. Existing instances keep their resolved image. Local provisioning stays off in this stack.

Docker templates have a private `/workspace` and may bind explicitly approved existing host directories. Mount sources resolve in the host Engine filesystem namespace and need permissions suitable for the container user. Environment deletion preserves these external paths. The hosted sandbox providers and external envd targets are also available; the development-only `local` provider is not offered.

Environment containers run on the host Engine outside this Compose project. Delete them through Console or the API before removing the stack.

## Data

`postgres`, `redis` and `service-data` persist across `stop`/`up` and container recreation. `service-data` holds the Service objects under `/app/var/objects` and the credential encryption key, generated at `/app/var/encryption.key` on first start; stored provider and connection credentials cannot be decrypted without it. `down` retains the volumes; `down -v` destroys the deployment's data.

## Backups and upgrades

Back up with the Service stopped, so the database and objects agree. The commands write `a13n-service.dump` and `a13n-service-data.tgz` to the current directory; `COMPOSE_FILE` names the Compose file for each of them:

```sh
export COMPOSE_FILE=deploy/docker/compose/a13n-service.yaml
docker compose stop service
docker compose exec -T postgres pg_dump -U a13n_service -Fc a13n_service > a13n-service.dump
docker compose run --rm --no-deps -v "$PWD:/backup" --entrypoint tar service -czf /backup/a13n-service-data.tgz -C /app/var .
docker compose start service
```

Restore into a new stack before its Service first starts: load both backups, then start everything.

```sh
docker compose up -d --wait postgres
docker compose exec -T postgres pg_restore -U a13n_service -d a13n_service < a13n-service.dump
docker compose run --rm --no-deps -v "$PWD:/backup" --entrypoint tar service -xzf /backup/a13n-service-data.tgz -C /app/var
docker compose up -d --wait
```

To upgrade, back up, then run `docker compose up -d --wait` with the new image: from a checkout, after rebuilding it; with a release's file, after replacing it with the new release's and carrying over your `environment` changes. Compose recreates the Service, which migrates the schema at startup. A migrated schema stops older images at startup; to return to one, restore the backup taken before the upgrade.
