# Service quickstart

Run Service on your machine, connect a model, and get your first agent response in Console. Already have a running Service? [Use your team's platform](use-platform.md) or [connect your application](connect-application.md).

You need Docker with Docker Compose and a model provider API key. No Python, Node.js, or source build is required. Model calls use your provider account and may incur charges.

## Try locally with Docker Compose

Download [`a13n-service-quickstart.yaml`](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service-quickstart.yaml) using GitHub's **Download raw file** button. Save it in an empty directory, open a terminal there, and start it:

```sh
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

Open <http://127.0.0.1:8080> and sign in with **`admin@example.com` / `local-public-password-123`**. The administrator, organization, and workspace are already created. Continue at [Add a model](#add-a-model); bring your own model provider credentials for real responses.

**Keep this trial on your own machine:** its administrator password is public. It binds only to loopback, does not mount the host Docker socket, and preserves data and credentials across restarts. The [Compose guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#local-quickstart) covers the stack in detail.

## Add a model

1. Open **Models → Add model → Connect a new provider**, choose the provider type (for example OpenAI or Anthropic) and enter its API key.
2. Select **Connect provider**, then choose a model from the catalog or select **Custom model** and enter an upstream model ID.
3. Give the model a name, check the upstream ID and API, then select **Add model**. For an OpenAI-compatible endpoint, choose the API it supports, such as **OpenAI Chat Completions**. Choose a model available to your provider account.

Outbound requests reject private addresses and plain HTTP by default. To use a model server on your own network, allow it first; see [outbound requests](configuration.md#outbound-requests). See [Models](models.md) for every provider type.

## Create and try an agent

1. Open **Agents → Create agent**, name it `My first agent`, choose your model, and enter instructions such as `You are a helpful assistant. Answer clearly and briefly.`
2. Save it. Every save creates an immutable version.
3. Choose **Try agent** and send `Give me three ideas for a useful agent I could build.` You should see the response stream into the conversation.

This first conversation needs no execution environment, tools, or memory setup. Those can be added after the model connection works.

Continue with the [Console guide](use-platform.md) for follow-ups, approvals, questions, and files.

## Use the API

Follow [Connect your application](connect-application.md) to create a workspace API key, select an agent, submit a message, and read the result. Choose a [Service SDK or remote CLI](sdks.md) for language-specific integration.

## Stop, resume, or reset the trial

Run these commands in the directory containing your quickstart file:

```sh
# Stop containers, keeping accounts, conversations, credentials, and files.
docker compose -f a13n-service-quickstart.yaml down

# Resume with the same data.
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

Keep the same Compose project and its volumes. Initialization runs again safely and does not restore the public password if you changed it.

To **permanently delete all trial data**, run `docker compose -f a13n-service-quickstart.yaml down --volumes`. The next start creates the public trial account again.

## Troubleshooting

- **Port 8080 is in use:** run `A13N_PORT=8081 docker compose -f a13n-service-quickstart.yaml up -d --wait`, then open <http://127.0.0.1:8081>. Keep using that port when resuming.
- **Startup does not finish:** inspect `docker compose -f a13n-service-quickstart.yaml ps -a` and `docker compose -f a13n-service-quickstart.yaml logs init service`. The initializer must complete successfully before Service starts.
- **The trial password no longer works:** existing data is preserved, including password changes. Sign in with the password you set; restarting does not reset it.
- **A model cannot respond:** check its provider credentials, upstream model ID, and API choice. Catalog entries do not guarantee access through your provider account. Private or HTTP endpoints also require the explicit [outbound request settings](configuration.md#outbound-requests).

## Deploy the Service

For your own deployment, use your own administrator credentials rather than the public trial account. The Service ships as the `a13n-service` Python package, the `ghcr.io/converge-ai-labs/a13n-service` image for `linux/amd64` and `linux/arm64`, and the Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`. Every deployment needs PostgreSQL, Redis and an encryption key; see [Configure Service](configuration.md#required-infrastructure). Two deployment guides are maintained in the repository:

- [Single host with Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker): Service, PostgreSQL, Redis and Console on one machine, with Docker environments on the host's Docker Engine.
- [Kubernetes with Helm](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes): separate control and worker Deployments and a migration Job, with values for a local kind cluster.

Follow either guide until Service reports ready at `/readyz`.

## Create the first administrator

This step is for an uninitialized deployment, not the pre-initialized local trial above. Open Console at the Service's public URL. Until Service is initialized, Console asks for the first administrator's email and a password of at least 12 characters instead of a sign-in; creating it signs you in. This creates the first organization, its workspace and the administrator, once: afterwards Console shows the sign-in, and further people join through [invitations](identity.md#invitations).

Whoever reaches an uninitialized Service first becomes its administrator. When a new deployment is reachable by others before you open it, create the administrator with the operator command `bootstrap` instead, run where the Service's configuration is available (inside the Service container for the deployments above):

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

It prompts for the password and prints the new organization, workspace and user IDs as JSON. Browser requests are accepted only from the Service's public origin; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable. Continue at [Add a model](#add-a-model).
