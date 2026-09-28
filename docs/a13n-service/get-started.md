# Service quickstart

Run Service on your machine, connect a model, and get your first agent response in Console. Then use the same agent from your application through the API.

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

You can send another message, stop the run, or answer an approval or question in the conversation. To use file and terminal tools, add an [environment template](environments.md#templates) to the agent.

## Use the API

Use a [Service SDK or the remote CLI](sdks.md), or call the API directly with curl:

Create an API key under **Workspace settings → My API keys** and export it with the Service URL. Requests with the key act in its workspace, so their paths name no workspace:

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

Find the key of the model you added under **Models** or with `GET /api/v1/models`. Set `MODEL_KEY` to that key before creating an agent:

```sh
MODEL_KEY=your-model-key
curl -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d "{\"name\": \"Helper\", \"config\": {\"model\": \"$MODEL_KEY\", \"instructions\": \"Answer briefly.\"}}"
```

Set `AGENT_ID` to the returned `id` and generate a key for this message:

```sh
AGENT_ID=ap_replace_with_the_returned_id
MESSAGE_KEY=$(uuidgen)
```

Start a conversation. To retry after a lost response, repeat this request with the same `MESSAGE_KEY`:

```sh
curl -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\": \"$AGENT_ID\", \"payload\": {\"content\": [{\"type\": \"text\", \"text\": \"What is a13n?\"}]}}"
```

The response contains `thread`, `entry`, and possibly `run`. If a run started, set `RUN_ID` to its `id` and read it until its status is `completed`, `waiting`, `failed`, or `cancelled`; a completed run's answer is in `output`:

```sh
RUN_ID=run_replace_with_the_returned_id
curl "$A13N_URL/api/v1/runs/$RUN_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

Instead of polling, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) or subscribe to [webhooks](files-and-webhooks.md#webhooks). Continue the conversation with `POST …/threads/{thread_id}/inbox`; see [Agents, threads and runs](agents-and-runs.md).

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
