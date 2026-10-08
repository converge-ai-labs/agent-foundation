---
title: Service quickstart
sidebarTitle: Quickstart
description: Run Service locally with Docker Compose, connect a model, and get a first agent response.
---

You need Docker with Docker Compose and a model provider API key. No Python, Node.js, or source build is required. Model calls use your provider account and may incur charges.

> [!TIP]
> Already have a running Service? [Use your team's platform](use-platform.md) or [connect your application](connect-application.md).

## Start with Docker Compose

Download [`a13n-service.yaml`](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service.yaml) into an empty directory and start the stack. You can copy these commands without cloning the repository or installing Make:

```sh
mkdir a13n-service
cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

Service, Console, PostgreSQL, and Redis start together. The stack mounts the host Docker socket and automatically adds a Docker environment provider and a `Linux Sandbox` environment template to each workspace. It publishes only <http://127.0.0.1:8080>. The source file uses the published `latest` image; release assets pin a release version.

If you already have a repository checkout, `make compose-up` from its root starts the same stack and prints the Console URL.

## Register your administrator account

Open <http://127.0.0.1:8080>. **On the first launch, register an administrator account instead of signing in:** enter your email and choose a password of at least **8 characters**. There is no default administrator email or password for this stack.

Registration creates the first administrator, organization, and workspace and signs you in automatically. Continue at [Add a model](#add-a-model). On later visits, sign in with the email and password you registered. Restarts preserve the account and data; additional users join through [invitations](identity.md#invitations).

Whoever registers first on an uninitialized Service becomes its administrator. Keep the stack on loopback while setting it up; for a deployment reachable by others before you open it, use [operator bootstrap](#initialize-a-shared-deployment) first. The [Compose guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker) covers host Docker access and deployment configuration.

## Add a model

1. Open **Models → Add model → Connect a new provider**.
2. Choose the provider type, for example OpenAI or Anthropic.
3. Enter its API key.
4. Select **Connect provider**, then choose a model from the catalog or select **Custom model** and enter an upstream model ID.
5. Give the model a name, check the upstream ID and API, then select **Add model**. For an OpenAI-compatible endpoint, choose the API it supports, such as **OpenAI Chat Completions**. Choose a model available to your provider account.

Outbound requests require HTTPS by default. To use a plain-HTTP model server, allow its exact origin first; see [outbound requests](configuration.md#outbound-requests). See [Models](models.md) for every provider type.

## Create and try an agent

1. Open **Agents → Create manually**, name it `My first agent`, choose your model, and enter instructions such as `Answer clearly and briefly. Suggest concrete next steps.`
2. Select **Create agent**. The initial configuration is saved as an immutable revision under **Versions**.
3. Choose **Try agent** and send `Give me three ideas for a useful agent I could build.` You should see the response stream into the conversation.

This first conversation needs no execution environment, tools, or memory setup. Those can be added after the model connection works.

Continue with the [Console guide](use-platform.md) for follow-ups, approvals, questions, and files.

## Use the API

Follow [Connect your application](connect-application.md) to create a workspace API key, select an agent, submit a message, and read the result. Choose a [Service SDK or remote CLI](sdks.md) for language-specific integration.

## Stop, resume, or reset the stack

Run these commands in the directory containing `a13n-service.yaml`:

```sh
# Stop containers, keeping accounts, conversations, credentials, and files.
docker compose -f a13n-service.yaml down

# Resume with the same data.
docker compose -f a13n-service.yaml up -d --wait
```

Keep the same Compose project and volumes to preserve your data. To refresh the images, add `--pull always` to the startup command; see the [Compose upgrade guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades) before upgrading an existing deployment.

To **permanently delete all stack data**, run `docker compose -f a13n-service.yaml down --volumes`. On the next start, open Console and register a new administrator account.

## Troubleshooting

- **Port 8080 is in use:** run `A13N_PORT=8081 docker compose -f a13n-service.yaml up -d --wait`, then open <http://127.0.0.1:8081>. Keep using that port when resuming.
- **Startup does not finish:** inspect `docker compose -f a13n-service.yaml ps -a` and `docker compose -f a13n-service.yaml logs service`. Service must finish database migration and become ready before you can use Console.
- **Console shows sign-in instead of registration:** the Service already has an administrator. Sign in with the account you registered; restarting does not reset it.
- **A model cannot respond:** check its provider credentials, upstream model ID, and API choice. Catalog entries do not guarantee access through your provider account. Plain-HTTP endpoints require an explicit origin allowance in [outbound request settings](configuration.md#outbound-requests).

## Deploy the Service

For shared deployments, configure the public URL and access before exposing Service. Service ships as the `a13n-service` Python package, the `ghcr.io/converge-ai-labs/a13n-service` image for `linux/amd64` and `linux/arm64`, and the Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`. Every deployment needs a public URL, PostgreSQL, Redis, shared object storage and an encryption key; see [Configure Service](configuration.md#required-infrastructure). Two deployment guides are maintained in the repository:

- [Single host with Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker): Service, PostgreSQL, Redis and Console on one machine, with Docker environments on the host's Docker Engine.
- [Kubernetes with Helm](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes): separate control and worker Deployments and a migration Job, with values for a local kind cluster.

Follow either guide until Service reports ready at `/readyz`.

## Initialize a shared deployment

When others can reach a new deployment before you open Console, create the administrator with `bootstrap` before exposing registration.

Run this where the Service's configuration is available (inside the Service container for the deployments above):

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

It prompts for the password and prints the new organization, workspace and user IDs as JSON. Browser requests are accepted only from the Service's public origin; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable. Continue at [Add a model](#add-a-model).
