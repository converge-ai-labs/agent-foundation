# Get started

This guide deploys the Service, creates the first administrator, connects a model and runs an agent, first in Console and then through the HTTP API.

## Deploy the Service

The Service is published as the `a13n-service` Python package, the `ghcr.io/converge-ai-labs/a13n-service` image for `linux/amd64` and `linux/arm64`, and the Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`, each at the Service's version. Every deployment needs PostgreSQL, Redis and an encryption key; see [Configure Service](configuration.md#required-infrastructure). Two ready-made deployments are maintained in the repository:

- [Single host with Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose): the Service, PostgreSQL, Redis and Console on one machine, with Docker environments on the host's Docker Engine.
- [Kubernetes with Helm](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes): separate control and worker Deployments and a migration Job, with values for a local kind cluster, EKS and GKE.

Follow either guide until the Service reports ready at `/readyz`.

## Create the first administrator

Open Console at the Service's public URL. Until the Service is initialized, Console asks for the first administrator's email and a password of at least 12 characters instead of a sign-in; creating it signs you in. This creates the first organization and workspace, both with the key `default`, and the administrator, once: afterwards Console shows the sign-in, and further people join through [invitations](identity.md#invitations).

Whoever reaches an uninitialized Service first becomes its administrator. When a new deployment is reachable by others before you open it, create the administrator with the operator command `bootstrap` instead, run where the Service's configuration is available (inside the Service container for the deployments above):

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

It prompts for the password and prints the new organization, workspace and user IDs as JSON. Browser requests are accepted only from the Service's public origin; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable.

## Add a model

1. Open **Models → Add model → Connect a new provider**, choose the provider type (for example OpenAI or Anthropic) and enter its API key.
2. Choose a model from the model catalog, or enter a model ID.

Outbound requests reject private addresses and plain HTTP by default. To use a model server on your own network, allow it first; see [outbound requests](configuration.md#outbound-requests). See [Models](models.md) for every provider type.

## Create and try an agent

1. Open **Agents → Create agent**, give it a name, choose the model and write its instructions.
2. Save it. Every save creates an immutable version.
3. Choose **Try agent**, or open **New conversation** and pick the agent, then send a message.

The conversation shows the agent's reasoning, tool calls and answer as they stream. Send more messages while it works to guide it, or stop it. When the agent asks for approval or a question, answer in the conversation. The file and terminal tools need an [environment](environments.md); add an environment template to the agent to give each conversation its own sandbox.

## Use the API

Create an API key under **Workspace settings → My API keys** and export it with the Service URL. API paths accept the workspace key in place of its ID:

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_... WORKSPACE=default
```

Create an agent from a model ID (`mdl_…`, shown on the model's page or by `GET /api/v1/organizations/{organization_id}/models`):

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "helper", "name": "Helper",
       "config": {"model": {"model_id": "mdl_..."}, "instructions": "Answer briefly."}}'
```

Start a conversation with its first message, naming the agent by the `id` the creation returned. The `Idempotency-Key` makes a retry after a lost response safe:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"agent_id": "ap_...", "payload": {"content": [{"type": "text", "text": "What is a13n?"}]}}'
```

The response holds the new `thread`, the message's inbox `entry` and the `run` it started. Read the run until its `status` is `completed`, `waiting`, `failed` or `cancelled`; a completed run's answer is in `output`:

```sh
curl "$A13N_URL/api/v1/workspaces/$WORKSPACE/runs/run_..." -H "Authorization: Bearer $A13N_API_KEY"
```

Instead of polling, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) or subscribe to [webhooks](files-and-webhooks.md#webhooks). Continue the conversation with `POST …/threads/{thread_id}/inbox`; see [Agents, threads and runs](agents-and-runs.md).
