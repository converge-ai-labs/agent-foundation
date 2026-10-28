---
title: Service quickstart
sidebarTitle: Quickstart
description: Start Service with Docker Compose and get a response from your first agent.
---

This guide takes you from an empty directory to an agent conversation in Console. Have Docker with Docker Compose, `curl`, and a model provider API key ready. The stack uses published images, so you need no Python, Node.js, or source build. Model calls use your provider account and may incur charges.

Already have access to Service? Start with [Use Console](use-platform.md) or [Connect your application](connect-application.md).

## Start with Docker Compose

Run these commands in a terminal. They create a directory, download the Compose file, and wait for the stack to become healthy:

```sh
mkdir a13n-service
cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
curl -fsS http://127.0.0.1:8080/readyz
```

The readiness response should contain `"status":"ready"`. Open <http://127.0.0.1:8080> to use Console. If startup fails, use [Troubleshooting](#troubleshooting) before continuing.

This stack runs Service, Console, PostgreSQL, and Redis. It publishes only the loopback address above and mounts the host Docker socket, granting Service host Docker authority. With that Engine reachable, Service prepares a Docker provider and `Linux Sandbox` environment template for each workspace. Your first model-only conversation will not need a sandbox.

The [source Compose file](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service.yaml) selects the published `latest` image; release assets pin their release version. From a repository checkout, `make compose-up` starts the same stack and prints its Console URL.

## Register your administrator account

1. Open Console and register with your email and a password of at least **8 characters**. There is no default administrator account.
2. After automatic sign-in, continue in the newly created organization and workspace.
3. Keep these credentials for later visits. Restarts preserve your account and data; other users join through [invitations](identity.md#invitations).

The first person to register becomes the administrator. Keep this local stack on loopback during setup. For a deployment others can reach before you open Console, [initialize it with the operator command](#initialize-a-shared-deployment) before exposing it.

## Add a model

1. Open **Models → Add model**. With no provider connected, the connection form opens directly; otherwise, select **Connect a new provider**.
2. Choose a provider type, such as OpenAI or Anthropic, enter its API key, and select **Connect provider**.
3. Choose a model from the catalog, or select **Custom model** and enter its upstream model ID.
4. Name the model and check its upstream ID and API, then select **Add model**. For an OpenAI-compatible server, select the API it supports, such as **OpenAI Chat Completions**.

Choose a model your provider account can use. Outbound requests require HTTPS by default; allow a plain-HTTP server's exact origin in [outbound request settings](configuration.md#outbound-requests) before using it. [Models](models.md) covers the available provider types.

## Create and try an agent

1. Open **Agents → Create manually**.
2. Name it `Project notes`, select your model, and enter these instructions: `Turn project notes into two concrete next steps. Keep the answer brief.`
3. Select **Create agent**. Console saves the initial configuration as an immutable revision under **Versions**.
4. Choose **Try agent** and send: `Draft ready; review pending; release planned for Friday. What should I do next?`
5. Wait for the response, then follow up with: `Make the first step more specific.`

You now have a saved agent and a thread that retains the discussion. The exact wording depends on your model; check that the reply addresses the notes and that the follow-up uses the preceding context. This path needs only a model and instructions. Add tools, environments, and memory when the task needs them.

Continue with [Use Console](use-platform.md) to return to the thread, create branches, and handle questions or approvals.

## Use the API

Open your agent's **Connect to app** action for its ID and your Service URL. [Connect your application](connect-application.md) walks through credentials, a first message, and reading the result. Use an [SDK or remote CLI](sdks.md) for language-specific integration.

## Stop, resume, or reset the stack

Run these commands from the directory containing `a13n-service.yaml`:

```sh
# Stop containers and keep the data volumes.
docker compose -f a13n-service.yaml down

# Start again with the same accounts, conversations, credentials, and files.
docker compose -f a13n-service.yaml up -d --wait
```

Keep the same Compose project and volumes. To upgrade, follow the [backup and upgrade procedure](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades), then add `--pull always` when starting to refresh images.

For a reset, first [delete managed environments](environments.md#stop-rename-and-delete) through Service. Their containers live outside the Compose stack; Service needs its records to clean them up. Then run `docker compose -f a13n-service.yaml down --volumes` to **permanently delete all stack data**. The next start requires a new administrator registration.

## Troubleshooting

| What you see                              | What to do                                                                                                                                                                         |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port 8080 is occupied                     | Start with `A13N_PORT=8081 docker compose -f a13n-service.yaml up -d --wait`, then open <http://127.0.0.1:8081>. Keep that port setting on later starts.                           |
| Startup does not complete                 | Inspect `docker compose -f a13n-service.yaml ps -a` and `docker compose -f a13n-service.yaml logs service`. Service must finish migration and become ready.                        |
| Readiness includes `"degraded":["redis"]` | Check the Redis container and connection. Service remains ready, with Redis-backed acceleration degraded.                                                                          |
| Console asks you to sign in               | The deployment is already initialized. Use your registered account; restarting does not reset it.                                                                                  |
| The model cannot respond                  | Check the provider key, upstream model ID, selected API, and account access. For a plain-HTTP endpoint, check its explicit [origin allowance](configuration.md#outbound-requests). |

## Deploy the Service

For a shared deployment, configure the public URL and access before exposing Service. Supply PostgreSQL, Redis, shared object storage, and an encryption key as described in [required infrastructure](configuration.md#required-infrastructure).

Choose a deployment guide:

- [Single host with Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker): run the stack on one machine and use its Docker Engine for environments.
- [Kubernetes with Helm](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes): run separate control and worker Deployments with a migration Job. The guide includes values for a local kind cluster.

Service ships as the `a13n-service` Python package, the `ghcr.io/converge-ai-labs/a13n-service` image for `linux/amd64` and `linux/arm64`, and the Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`. Follow the chosen guide until `/readyz` reports ready.

## Initialize a shared deployment

Use `bootstrap` to create the administrator before making an uninitialized deployment reachable by others. For the Compose stack above, run this from the directory containing its YAML file after Service has started:

```sh
docker compose -f a13n-service.yaml exec service \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
docker compose -f a13n-service.yaml restart service
```

Replace `admin@example.com` with your email. The command prompts for a password and prints the organization, workspace, and user IDs as JSON. The restart prepares workspace defaults created by CLI bootstrap. In another deployment, run the same `a13n-service` command inside the configured Service container and arrange its next startup through that deployment's tooling.

Sign in at the configured public URL and [add a model](#add-a-model). Browser authentication requests use the Service's public origin; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable.
