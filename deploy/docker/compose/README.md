# Single-host Service with native Docker

`a13n-service.yaml` runs the Service with every role in one process (`run --role all`), PostgreSQL and Redis. Only the Service is published, on `127.0.0.1:8080`. It serves the Console and the API from one origin, which browsers, API clients and SDKs share. The Service migrates its schema when it starts. It uses the host Docker Engine through `/var/run/docker.sock`: the container starts as root only to join the group that owns the socket, then runs as the image's non-root `app` user. Access to this socket grants host Docker authority, so deploy only where the Service is trusted with that authority.

Build the images and start the stack from the repository root:

```sh
make image-a13n-service image-docker-environment
docker compose -f deploy/docker/compose/a13n-service.yaml up -d --wait
```

Without a checkout, download `a13n-service.yaml` from a Service release and run `docker compose -f a13n-service.yaml up -d --wait` in its directory. The release's copy runs that release's published image, for `linux/amd64` or `linux/arm64`.

Open <http://127.0.0.1:8080> and create the first administrator with an email and a password of at least 12 characters; this signs you in. Only the first visitor can do this, and only the host itself reaches the published port. To create the administrator without a browser, run the `bootstrap` command instead, which prompts for the password:

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml exec service \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

`A13N_PORT` publishes another port and moves the public URL with it, and `A13N_SERVICE_IMAGE` selects another image tag; the default is the local build above. <http://127.0.0.1:8080/readyz> reports Service readiness.

`make compose-smoke` checks the local image with this Compose file on a disposable project and port: it creates the first administrator, stores a credential, restarts the Service, signs in again, confirms the generated key survived, then removes the project and its volumes.

## Configuration

The Service's `environment` in the Compose file sets its settings as `A13N_<SECTION>__<FIELD>` variables over the image's defaults: the public URL, PostgreSQL, Redis and the encryption key file. Add other settings the same way, with list values as JSON; see the [configuration reference](../../../docs/a13n-service/configuration-reference.md) for every setting. The included database password protects only this unpublished local database. The Service reads its configuration at startup: after an edit, run `up -d --wait` again, which recreates the Service.

Outbound provider requests reject private addresses and plain HTTP by default. To use, for example, a model server on the Docker host, allow it explicitly in the Service's `environment`:

```yaml
A13N_PROVIDERS__PRIVATE_DOMAINS: '["host.docker.internal"]'
A13N_PROVIDERS__HTTP_ORIGINS: '["http://host.docker.internal:11434"]'
```

## Docker environments

Add an environment provider of type `docker` in Console or through `POST /api/v1/organizations/{organization_id}/environment-providers`; its default Engine address is the mounted socket. Environment templates choose the image in their recipe. The default, `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`, is published from `main` for `linux/amd64` and `linux/arm64`, and `make image-docker-environment` builds `a13n-docker-environment:local`. A locally built image is immediately available to templates using the same host Engine, and missing images are pulled when first needed. Pin a digest for reproducibility. Rebuilding or pulling a tag does not recreate existing environments; delete an environment and create another to use a new image version.

Docker templates have a private `/workspace` and may bind explicitly approved existing host directories. Mount sources resolve in the host Engine filesystem namespace and need permissions suitable for the container user. Environment deletion preserves these external paths. The hosted sandbox providers and external envd targets are also available; the development-only `local` provider is not offered.

Environment containers run on the host Engine outside this Compose project and carry the label `a13n.environment=<environment ID>`. Unmount and delete environments through Console or the API before removing the stack.

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
