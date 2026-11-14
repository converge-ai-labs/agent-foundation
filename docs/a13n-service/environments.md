---
title: Environments
description: Create a workspace for file and command tasks, reuse a machine, or give an agent a desktop.
---

Give an agent an environment when its task needs files, commands or a desktop. Start with one template and one thread: ask the agent to write a file, then read it in a follow-up message. That checks both execution and file reuse.

Three resources connect the workflow:

| Resource     | What you choose                                         | Result                             |
| ------------ | ------------------------------------------------------- | ---------------------------------- |
| Provider     | A Docker engine or cloud account                        | Service can manage resources there |
| Template     | Image, resources, environment variables and idle policy | A recipe for new managed instances |
| Thread mount | An instance, name, directory and permissions            | Later runs can use that instance   |

For a computer you already operate, [connect Envd outbound](#connect-a-client-computer), or [register its HTTP endpoint](#register-an-external-target) if Service can reach it directly. You own its lifecycle. Every accepted run captures its mounts and permissions, so changes apply to later runs.

## Outbound WebSocket for managed sandboxes

Set `execution: "envd_ws"` in a Docker, E2B, Daytona, Modal, Sprites or Vercel template recipe to let its Envd connect to Control. The existing Provider still manages creation, stop/start and destruction. Runs continue selecting the same environment ID; no client registration or ordinary API token is needed inside the sandbox.

Standalone Console desktop preview is currently unavailable for `envd_ws`; the button explains this restriction. Agent Computer tools remain available when the daemon and permissions support them. If an execution Session expires while the device stays online, the failed tool reports it and the run can prepare a new Session at its next tool use. Previous commands are not replayed, and old process handles are not restored.

Configure `server.public_url` as an HTTPS origin reachable from the sandbox and allow WebSocket forwarding. Use an updated compatible Envd image/template with `a13n-sandbox-entrypoint`. Automatic preparation supports headless and desktop profiles: E2B can install into a compatible selected template, Vercel installs on the final persistent Ubuntu target, and Sprites installs on the final Sprite. Explicit custom image/template/snapshot inputs keep their preinstalled prerequisites. In Console, choose the execution connection in template configuration (Docker: select a custom environment).

Service supplies a private per-environment connection credential and waits for EIP initialization before marking creation/start ready. A failed connection preserves the allocated sandbox for retry or cleanup. Worker tools use the outbound connection without reaching an Envd HTTP port. Provider lifecycle APIs, human desktop access and metrics retain their own network requirements. Binary files and screenshots use Redis Streams between Worker and Control, and the existing WebSocket between Control and Envd.

Existing templates keep their native or HTTP default. This execution mode requires Service; standalone Harness provider connectors reject it. Changing a template affects new environments, not already-created instances.

## Envd installation and upgrades

Docker, E2B, Modal, Daytona, Vercel and Sprites recipes accept `envd_management`:

| Value          | Installation and startup ownership                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `managed`      | Service installs its pinned release and restarts the runtime during preparation.                                            |
| `preinstalled` | Supply Envd and its boot command yourself. Service checks readiness without installing, replacing startup, or upgrading it. |

Automatic Envd preparation and official Docker Envd images default to `managed`. Explicit custom images/templates/snapshots default to `preinstalled`. Automatic asset selectors require `managed`; conflicting settings are rejected. Native execution does not accept `managed`. Kubernetes keeps its operator-authored Pod and init-container boot recipe.

For example, E2B can prepare a compatible selected template and manage its Envd binary:

```json
{"execution":"envd_http","template":"my-debian13-template","preparation":"headless","envd_management":"managed"}
```

Choose `preinstalled` for a compatible custom runtime you maintain yourself:

- E2B: a custom `template` and `preparation: "preinstalled"`.
- Modal: a custom `image` and `startup_command`, for example `["/usr/local/bin/start-envd"]`. Service uses this argv unchanged on create and snapshot resume.
- Daytona: a custom snapshot whose boot command starts Envd.
- Vercel: a custom `snapshot` and `startup_command`, for example `["/usr/local/bin/start-envd"]`. The command must start the runtime in the background and return. Service executes this argv as root with the instance bootstrap environment on create and native session resume. A recorded job per native session lets management rejoin startup after a lost reply without replaying the command; filesystem snapshots do not preserve processes.
- Sprites: `envd_http` with your own installed runtime, Device identity and configured service; Service does not register its standard launcher. `preinstalled` with `envd_ws` is rejected because private outbound bootstrap currently requires the Service launcher.
- Docker: an image with your authored boot command.

Modal/Daytona `base_image` selects a supported custom base for automatic managed preparation. Opting an existing custom runtime into `managed` requires the standard root-owned install/runtime paths, GNU/Linux installer prerequisites and native supervision; the flag does not replace its entrypoint or install a desktop.

Service updates on creation, start/resume, and the first use of an existing managed instance after the Service pin advances. It claims the normal durable preparation operation before execution, installs the verified release, requests restart, and checks authenticated EIP readiness. Concurrent callers join the same operation. Human desktop reservations and viewers defer a first-use update. Idle instances are not updated by a timer, and Service never selects `latest`.

**An upgrade ends existing Envd Sessions and their processes and output handles.** Workspace files follow the provider's normal persistence rules. Session recovery can open a new Session; commands are never replayed. Installation failure keeps the previous binary and records the failed preparation. Inspect the target's protected `/var/lib/a13n-envd/update-<version>/output` and reconcile its outcome. Console offers **Retry preparation** for a confirmed failed upgrade; API clients use `POST /api/v1/environments/{id}/retry-creation` with the current `If-Match`. The retry retains the instance and selected version, starts a new guest attempt, and rejects active or unknown outcomes and confirmed instance loss. A failed upgrade keeps a previously ready managed instance in `starting`, unavailable until the explicit retry succeeds; it does not start failed-initial-preparation reclamation. Fix a missing release or incompatible runtime before retrying. Confirmed native loss requires replacement or deletion. A lost receipt is not evidence that a command did not run.

External service managers run `a13n-envd daemon` and own restarts. Without such a manager, prepared runtimes use `a13n-envd supervise`; Service asks that native supervisor to restart after installing the binary. [Envd operations](../a13n-envd/operations.md#restart-after-installing-a-release) describes the local restart command.

## Workspace provisioning

Check **Environments → Templates** before creating anything. The single-host Compose deployment with a Docker socket prepares a **Docker** provider and **Linux Sandbox** template when the Engine is reachable. Select that template for the first file task. Its image includes Python, Node.js, Git and common development tools; commands run as `sandbox`, with writable `/workspace` and `/tmp/a13n`.

The socket-free quickstart has no automatic Docker setup. For source development, `make dev` enables **Local** and creates a **Local Workspace** template under the checkout's `var/dev/environments`. Each instance gets its own subdirectory. Local commands run directly as the Service process's operating-system user, without isolation.

Generic Service settings leave both provisioning options off. To enable them or use a locally built image, follow [workspace provisioning configuration](workspace-provisioning.md#workspace-provisioning). The `all` role prepares missing defaults at workspace creation and startup. If setup fails, repair Engine access and restart. Successful provisioning preserves subsequent template edits, disabling and deletion. It creates configuration resources; an instance is allocated when requested.

## Providers

Add or edit an environment provider under **Workspace settings → Providers → Environment**. You need workspace `write` permission and access to the selected engine or cloud account. A template's **Manage providers** action opens the same list while preserving its draft.

1. Choose the runtime and enter its account settings and credentials.
2. Save the provider and leave it enabled.
3. For Docker, test the connection. This pings the Engine; creating an instance verifies image and execution setup.
4. Select the provider when creating a template.

![Console sandbox and environment provider catalog](../../.github/assets/console-sandbox-providers.webp)

The [provider guide](../environments/providers.md) covers Docker, E2B, Daytona, Modal, Vercel, Sprites, Runloop and Local setup. The [configuration reference](../environments/configuration-reference.md) owns their recipe fields. API clients create providers at `/api/v1/environment-providers` and obtain type schemas from `GET /api/v1/provider-types/environment`.

Docker defaults to the operator's `environments.docker_host`. A provider-specific address must be a remote TCP or HTTPS Engine permitted by the [outbound policy](configuration.md#outbound-requests). Engine access grants host-level authority. Host bind sources must be under `environments.docker_mount_roots`, which permits none by default; privileged containers and host namespaces are unavailable.

Cloud instances use the saved account and are billed there. Configure idle stop or deletion to bound their lifetime. Service renews supported ready sandboxes, but vendor hard limits still apply. A stopped instance can resume with its retained files; a lost instance requires recovery or replacement. Sprites has no explicit stop operation, so use idle deletion when needed.

When editing a provider, an omitted credential stays unchanged; a new value replaces it and `null` removes it. Retained template or instance references block provider deletion. Deleting an unused provider removes its saved configuration and credentials, not the underlying engine or cloud account.

## Kubernetes sandboxes

Kubernetes runs an instance as a native Pod with Envd in one selected container. The operator configures trusted cluster profiles, allowed namespaces and a sandbox ServiceAccount. Kubernetes then appears in the provider catalog. Workspace users select a profile and namespace; Kubernetes API credentials remain with the operator.

1. Ask the operator to configure `environments.kubernetes_clusters` on Control and Worker, or use the Helm opt-in described in [Kubernetes operator configuration](configuration.md#kubernetes-environments).
2. In **Workspace settings → Providers → Environment**, create a Kubernetes provider. Select the operator's cluster profile and enter an allowed namespace. For the Helm example, use `config: {"cluster":"in-cluster","namespace":"agent-sandboxes"}`. Save the provider, then open it and select **Check connection**.
3. Prepare the sandbox image as described below, then create a template. Choose **Direct Pod connection** or **Local port forwarding**. Use the cards to configure containers, resource requests and limits, variables, persistent storage and scheduling. **Lifecycle** controls reclamation separately. Ordinary setup does not require YAML or JSON; **Edit native JSON** exposes additional native fields and preserves them when you return to the form. The JSON below is the equivalent API template `config`.
4. On an agent with a working model, enable **Files** and **Shell** and select this default template. Start a conversation and ask it to write `hello.txt`, then read the file in a follow-up. For a one-run override, choose **Run options → Run environment → Create from template**.

```json
{
  "recipe": {
    "execution_container": "envd",
    "connection": "direct",
    "pod_template": {
      "spec": {
        "restartPolicy": "Never",
        "containers": [{"name": "envd", "image": "a13n-sandbox:local"}]
      }
    }
  },
  "reclamation": {
    "idle_delete_seconds": 1800,
    "preparation_seconds": 600,
    "failed_retention_seconds": 3600
  }
}
```

**Prepare the image before the first run.** The starter's `a13n-sandbox:local` is a local build, not a registry image. From this checkout, build and load it into your existing kind cluster (replace `my-cluster`):

```sh
make image-sandbox SANDBOX_IMAGE=a13n-sandbox:local
kind load docker-image a13n-sandbox:local --name my-cluster
```

For other clusters, build this checkout's sandbox for the node architecture, push it to your registry, and put that exact tag or digest in `pod_template.spec.containers[].image`:

```sh
make image-sandbox SANDBOX_IMAGE=registry.example.com/team/a13n-sandbox:reviewed
# Authenticate to your registry before pushing.
docker push registry.example.com/team/a13n-sandbox:reviewed
```

Configure native `imagePullSecrets` when the registry is private. The image must run Envd and honor Service's injected credential-file path; use the sandbox image built with the matching Service checkout. `make k8s-up K8S_ENVIRONMENTS=1` builds and loads its own image tag; read `sandbox_image` from `deployment.json` in its `K8S_STATE_DIR` rather than assuming it is `:local`. The default directory is `~/.config/a13n-service-kind`, with `-<cluster>` appended for a nondefault `K8S_CLUSTER`.

Keep resource requests and limits, init containers, sidecars, scheduling, security context and references to ConfigMaps or Secrets in `pod_template.spec`. Kubernetes admission validates these native fields. Service owns the Pod identity, sandbox ServiceAccount and private Envd credential injection. The selected execution container must start Envd; a Running Pod alone does not establish execution readiness.

`direct` connects to the Pod IP from Service, whether Service runs inside or outside the cluster. Outside the cluster, the operator supplies a kubeconfig profile on both Control and Worker. Both processes must reach Pod IPs for direct connections. For local debugging without Pod IP routing, `port_forward` uses an explicitly permitted kubeconfig profile and an installed `kubectl`; the tunnel is local to the process using it. Both modes currently use bearer-authenticated HTTP and require the profile's explicit `allow_plaintext_envd = true` opt-in. Keep that traffic on a trusted private network. A successful provider check verifies configuration, authentication and Pod-list access; it does not prove image pulls, Pod creation, scheduling, storage or Envd readiness.

For persistent files, add `recipe.storage` bindings with `name`, `mount_path`, and either a native `claim_template` or `existing_claim`. New claims default to `disposition: retain`; select `delete` to remove an owned claim during retirement. Existing claims remain borrowed, even if a binding asks for deletion. `shared_directory: true` uses an instance-specific directory on an existing claim and retains that directory. Referenced ConfigMaps and Secrets also remain borrowed. `recipe.network_policy` accepts a native NetworkPolicy template whose Pod selector Service supplies; enforcement requires a compatible cluster network implementation.

Kubernetes supports create, reuse and delete. Its reclamation settings are copied when an instance is reserved. The defaults delete after 30 minutes idle, bound preparation to 10 minutes, and retain a failed preparation for one hour. Kubernetes has no stop/resume operation. Deletion waits until owned resources are absent; a finalizer can keep the instance in `deleting`. Retained and borrowed resources remain visible in the retirement record.

Inspect the instance's Kubernetes panel for Pod UID, scheduling and container status, Events, storage disposition and Envd readiness. `GET /api/v1/environments/{id}/kubernetes/diagnostics` and `/kubernetes/logs` require workspace `run` permission. Logs are bounded and support a container and previous-container selection. These observations do not reset idle time. Use `PUT /api/v1/environments/{id}/debug-hold` with the current `If-Match` and `{"seconds":3600}` to postpone automatic retirement while debugging; `{"seconds":null}` clears it. A hold lasts at most one day. A replacement Pod with a different UID is target loss; create a new instance rather than adopting it silently.

## Run-only environment selection

The conversation's run options can create a fresh workspace or select an existing instance for one run. API clients submit `options.environment`:

```json
{"mode":"fresh","template_id":"envtpl_..."}
```

```json
{"mode":"existing","environment_id":"env_..."}
```

The accepted run captures the instance ID and permissions. Retries of that accepted run retain the same selection. The override replaces only that run's `workspace` mount and leaves the thread's desired mounts unchanged. Omitting it retains the normal thread and agent-default behavior. Selecting an unavailable existing instance fails instead of creating a replacement.

## Templates

Create the first workspace through Console:

1. Open **Environments → Templates → Create template**. Name it **Python sandbox**.
2. Choose Docker and an enabled provider. Set the resource limits and idle policy, then save. Saving a template starts no container.
3. Edit an agent that already has a working model. Enable its **Files** and **Shell** toolsets, select this default environment template, and save the agent configuration.
4. Start a new conversation. Ask: “Write `hello.txt` in the workspace with the text `Hello from Service`, then read it back.” Inspect the tool results.
5. Send a follow-up asking the agent to read the same file. It should use the same thread workspace.

The agent field is `default_environment_template_id`. On acceptance of a root-thread run without a `workspace` mount, Service reserves an instance and mounts it as `workspace`. A `reserved` instance has an ID but no vendor resource yet. First use prepares it. Reservations count toward the managed-environment quota.

To reuse an existing environment, set `agent.config.default_environment_id` instead. The two fields are mutually exclusive. A thread's explicit `workspace` mount takes precedence; changing the Agent default does not replace it or affect accepted runs. Client environments may be selected while offline. Client environments are usable by users and service accounts with `run` permission in the same workspace. External HTTP targets remain restricted to their registering principal. Child runs keep the spawning edge's environment policy.

For API setup, use the URL and workspace API key from [direct HTTP integration](connect-application.md#direct-http-integration). Replace `eprov_...` with your Docker provider ID. Creating or editing a template requires `write`:

```sh
curl -sS -X POST "$A13N_URL/api/v1/environment-templates" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Python sandbox","provider_id":"eprov_...",
       "config":{"recipe":{"cpus":2,"memory_gb":4},
                 "stop_after_seconds":1800,"delete_after_seconds":604800}}'
```

Save the returned template ID. This example stops idle instances after 30 minutes and deletes them after seven days. Both timers count from last use; `null` disables the corresponding policy. Idle deletion removes thread mounts and the instance's files. A thread with a default template can receive a new workspace on its next run.

Template updates use the template's ETag in `If-Match`. The recipe freezes when creation is first claimed, so edit it before that point or create a new instance. For providers with `stop_after_seconds` and `delete_after_seconds`, idle-policy edits apply immediately to existing instances. Kubernetes `reclamation` is captured at reservation; editing it affects new reservations. Disabling a template blocks new reservations and first creation of still-reserved instances; already-created instances keep working.

### Default environment variables

In the template's **Environment variables**, add ordinary command defaults such as `APP_MODE=agent`. The API location is `config.recipe.environment`:

```json
{"environment":{"APP_MODE":"agent","TOOL_ENDPOINT":"https://tools.example.com"}}
```

This is a recipe fragment. Commands and their child processes receive these values. [Execution policy](execution-options.md#execution-policy) and explicit command values can override them without changing the shared instance. Local also requires the names in `allowed_environment_keys`, unless that allowlist is unrestricted.

Use portable names and string values. Keep secrets in their credential or secret-binding controls: template variables are readable configuration. Platform bootstrap, desktop and telemetry names are reserved. Defaults freeze with the instance recipe; stop/start preserves them. Create a new instance to apply changed template defaults.

### Docker image versions

With `recipe.image` omitted, Service selects its reviewed `ghcr.io/converge-ai-labs/a13n-sandbox` image:

| Service build                                         | Image tag |
| ----------------------------------------------------- | --------- |
| Stable or RC                                          | `0.8.2`   |
| Source (`0.0.0`, including local suffixes) or `.devN` | `dev`     |

Sandbox versions belong to Envd and are independent of Service versions. Set an explicit tag or digest to choose another image. `pull_policy: if_missing` uses the Engine's local image or pulls it at first creation; `never` requires the image to be present. Provisioned templates save an explicit image, so upgrades preserve that choice.

Instances retain their resolved image across retries and upgrades. Recreate an instance to use a new image. Legacy handles without a saved image keep `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`; recreate those instances before removing the old image.

## Instances

Open **Environments → Instances** to inspect creation and use. API clients use `GET /api/v1/environments`, or fetch one instance by ID. Deleted records appear in listings only with `status=deleted`.

| Status                 | What to do                                               |
| ---------------------- | -------------------------------------------------------- |
| `reserved`             | Use it in a run to trigger preparation                   |
| `creating`, `starting` | Wait for readiness; inspect a reported failure           |
| `ready`                | Mount it and run the task                                |
| `stopping`, `stopped`  | Wait for stopping to finish; a later run starts it again |
| `deleting`, `deleted`  | Select another instance                                  |

A failure reports its code, message and whether the operation outcome is known. Service reconciles unfinished operations using their existing identity. For `provider_credential_changed`, restore access to the instance's original account. When a provider confirms `environment_lost` on a thread's template-backed `workspace` mount, the next run using that mount gets a fresh instance from the same template. Files and processes from the lost sandbox are gone. The run's usual `lazy_environment` setting controls when the new sandbox starts. A run already using the lost instance can fail.

Threads that shared the lost instance, including forks, use the same replacement as each resumes. If the instance is lost again, a returning thread joins the latest replacement. Replacing a lost instance uses its existing quota slot, even when the limit is one. If idle policy deletes the replacement, it frees that slot. The next eligible run needs a free slot to create another shared instance. If the workspace is full, the run is refused with `environment_limit`; the threads can try again when a slot is free.

If your application needs the original disk, set `"auto_recreate_lost_environment": false` when creating the session, or send it in `PATCH /api/v1/sessions/{id}` with the session's current `If-Match`. The default is `true`. Runs that select an exact instance, shared child agents, and mounts with other names continue to report their original instance's failure.

### Reserve a managed environment

Create an instance explicitly when you want to inspect it before a task or share it across threads. Replace the template placeholder:

```sh
curl -sS -X POST "$A13N_URL/api/v1/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"envtpl_...","name":"Shared data"}'
```

This operation requires `run` and starts creation immediately: the response is `creating`, followed by `ready` in the background. Save its `id` as `ENVIRONMENT`, inspect `GET /api/v1/environments/$ENVIRONMENT`, then mount it below. This differs from the lazy reservation made at run acceptance. Both count toward `environments.managed_count`; exceeding that limit returns `409 conflict` with reason `environment_limit`.

### Register an external target

1. Run an [HTTP Envd daemon](../environments/remote-envd.md#connect-to-an-existing-http-daemon) on the target computer with a stable device ID and token.
2. Make its endpoint reachable from Service workers and allow it in the outbound policy. Plain HTTP is accepted only on loopback; use HTTPS elsewhere.
3. Register its origin and token with workspace `write` permission:

```sh
curl -sS -X POST "$A13N_URL/api/v1/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"endpoint":"https://build-box.example.com:8443","token":"YOUR_ENVD_TOKEN","name":"Build box"}'
```

Service verifies the device, stores the token encrypted, and returns a `ready` instance. Mount it as the registering user or service account: external targets are private to that principal for execution. The owner or a workspace administrator can manage the registration.

To rotate the token or move the endpoint, `PATCH /api/v1/environments/{id}` with the environment's ETag. Include the token when changing the endpoint. Service verifies that the new connection reaches the same device; another device is refused with `provider_device_mismatch`. Deleting the registration forgets its token and leaves the computer running.

Each Thread owns a named Session binding on an Environment. Directory mounts using the same binding share its Session across Runs; different binding names select independent Sessions on the same Device. Final cleanup detaches the connection and keeps the recovery descriptor. Ordinary new work can open a fresh Session after confirmed loss. Retained Shell work requires its exact original Session and process; recovery never substitutes or replays the command. Endpoint and token rotation preserve the logical binding.

Set `session_binding` on a mount (default `default`) and use `session_configuration` to initialize its startup policy. Inspect or replace desired policy through `GET` or `PUT /api/v1/threads/{thread_id}/session-bindings/{environment_id}/{name}?workspace_id=…`; PUT uses the Thread ETag and a `configuration` object. Changing startup policy replaces the Session at admission only when no admitted Run or unfinished retained tool uses it; otherwise admission returns `environment_busy`. Directory selection remains mount-local.

### HTTPS credentials in commands

Create a workspace [egress credential](resources.md#egress-credentials), then reference its ID in Session configuration. For example:

```json
{
  "egress_mode": "controlled",
  "egress": {
    "destinations": { "mode": "allowlist", "hosts": ["api.github.com"] },
    "secrets": [
      {
        "env": "GH_TOKEN",
        "credential_id": "ecred_your_credential_id",
        "inject_hosts": ["api.github.com"]
      }
    ]
  }
}
```

Put this policy in an Agent's `default_session_configuration` to initialize new primary bindings, in a Thread binding's `configuration` for later Runs, or in `options.environment.session_configuration` for one Run. Existing Thread bindings take precedence over Agent defaults. A Run override does not rewrite the Thread's desired policy. Different policies cannot concurrently use the same binding.

The environment must support controlled Envd egress. Service checks current credentials before allowing commands; unsupported execution fails without putting real tokens into ordinary environment variables. Envd supplies placeholder values and substitutes them in HTTPS headers only for the configured hosts. Commands still construct the service's required header, for example `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user`. Keep TLS verification enabled and use the environment's configured trust. Host web and browser tools do not inherit these bindings.

The Agent receives the selected variable names, domains and usage instructions on every model request. Only one current copy is used; history compaction does not remove it. Instructions contain neither real values nor placeholders. Rotating the value does not change the command or these instructions.

Rotation saves the new value synchronously and updates active Sessions asynchronously. Existing Session owners also compare versions periodically (ten seconds by default), so lost notifications are retried. A retained background tool can keep synchronization active after its Run ends. Idle Sessions are not connected just to update a credential: they receive the latest version before commands at their next attach. Detach preserves the Session and its credentials; native close or expiry cleans them up.

Revocation prevents new use immediately at Service admission. Active Sessions remove their bindings asynchronously. An idle Session may still retain its old value until next attach or native cleanup; revoke the token at the upstream service when immediate invalidation is needed. A successful save or revoke response is not proof that all native Sessions have applied it.

### Upgrade to Session bindings

Before this schema cutover, finish or cancel and settle every admitted Run using environment mounts, including waiting Runs, and all retained environment or Shell tools. Worker drain or handoff alone is insufficient. Stop all old Controls and Workers, migrate, then start only the new version. The migration preserves registrations, provider state, files and historical Run captures, but does not import old Session recovery hints into the new binding heads. New work opens fresh Sessions. Do not mix old and new processes during this upgrade.

### Connect a client computer

Users and service accounts with `run` permission in this workspace can use both existing and newly registered client computers. Registration requires `write`; credential rotation, revocation and status changes still require the owner or a workspace administrator with `write`. File, Shell and desktop permissions remain limited by the registration, the Run and local Envd configuration.

Use **Environments → Instances → Connect my computer** to pair through your signed-in Console session. [Install the macOS launcher](../a13n-envd/installation.md#connect-this-computer-from-console), open it from the dialog, then enter the app verification code and approve. A terminal command is also available. No ordinary API key is needed for this flow. Once online, choose the registered environment in your Agent or conversation settings. The API-token workflow below remains available for automated setup.

Use this path when Envd can make an outbound connection to Control but Workers cannot dial the computer. You can register the environment and mount it on a thread before starting Envd.

When upgrading to the client-environment schema, drain active work and stop all old Controls and Workers before applying the migration. Existing managed and HTTP environments are preserved, but old versions cannot write the new schema. After any client registration exists, including a deleted one, use forward repair instead of downgrading the schema.

For automatic first registration, run `a13n-envd connect-service` with an ordinary API token, workspace ID and local working directory. See [Connect to Service](../a13n-envd/configuration.md#connect-to-service) for the command and credential recovery. The enrollment endpoint is `POST /api/v1/client-environments/connect?workspace_id=…`; it accepts the registration fields below plus optional `environment_id`. Without an ID, only an absent owner/workspace/device binding creates an environment. Supplying an ID requires that exact existing binding. Existing configuration is unchanged, and disabled, deleted or revoked registrations are rejected. A repeated request returns no token; explicit rotation handles lost delivery.

To register while the computer is offline or set a narrower permission ceiling before connecting:

1. As a principal with workspace `write` permission, send `POST /api/v1/client-environments?workspace_id=…` with the installation's persistent device ID, an existing directory and the permitted actions. For example:

   ```json
   {
     "device_id": "dev-example",
     "name": "Laptop",
     "default_working_directory": "/Users/example/project",
     "permission_ceiling": {"operations": ["environment.file.list", "environment.file.read_text"]}
   }
   ```

2. Save `client.environment.id` and put the returned `token` in a private local credential file (mode `0600` on Unix). The first registration returns 201; registering the same installation again returns 200 and no token. Keep a separate environment ID and token file for each Service origin, account and workspace. Use the explicit `rotate-credential` API with the current Environment ETag if the token is lost. The device ID identifies an installation, not physical hardware. Copying both the ID and the token copies the installation's access.

3. Run Envd with the same device ID and its normal local access policy. Set `A13N_ENVD_TRANSPORT=reverse_websocket`, `A13N_ENVD_REVERSE_WS_URL` to the Control `wss://` origin plus the returned `connection_path`, and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE` to the token file. [Envd configuration](../a13n-envd/configuration.md) describes the runtime directory, installation identity and local permission settings. Do not put the token in the URL.

4. [Mount the environment on the thread](#mount-environments-on-a-thread). A run keeps the directory and permissions selected at acceptance. `ready` means selectable; the first actual tool use checks the connection, opens a Session and verifies the directory. Device connection alone never mounts it on a run.

Use the `device-id` in Envd’s installation state directory, or have the installer generate and save an ID and supply the same value to registration and `A13N_ENVD_DEVICE_ID`. When Computer Use is enabled, Envd checks desktop permissions locally. Registration runs no test command, and a successful handshake does not prove that a particular executable is installed.

An offline tool reports that it did not dispatch. After such a failure, the next normal agent iteration can report that the selected mount has reconnected. Recovery does not repeat the command. On a process-capable mount, one agent `shell_exec` starts the process and waits briefly. The agent can later use `shell_info` or `shell_wait` with the returned reference. A wait timeout does not kill the process. If a reply is lost after dispatch, the result is unknown.

The Instances page shows when the connection state was checked. An offline computer remains registered. If envd reports `device_id_mismatch`, check the environment and pass its registered Device ID with `--device-id`. Press Ctrl+C in the envd terminal to disconnect.

Binary files and screenshots pass through Redis Streams, so Workers need no per-replica Control URL. Remove the obsolete `A13N_CLIENT_ENVIRONMENTS__TRANSFER_ORIGIN` setting when upgrading, drain active work and update Workers and Controls together. Device credentials never go to Workers.

Each transfer creates `a13n:client-environment:{environment_id}:transfer:{epoch}:{transfer_id}:w2c` and the corresponding `:c2w` key. They carry frames in opposite directions, retain at most eight unconsumed frames each, and expire after five minutes without activity. Successful close, cancellation or failure deletes them. `A13N_CLIENT_ENVIRONMENTS__TRANSFER_SECONDS` bounds each transfer, including slow-producer/consumer waits (default: 120 seconds). An interrupted transfer fails; it does not automatically resume or repeat a file commit.

### Stop, rename and delete

Read the environment first and send its ETag in `If-Match`. These operations require `write`:

| Operation                                                    | Result                                                         |
| ------------------------------------------------------------ | -------------------------------------------------------------- |
| `PATCH /api/v1/environments/{id}` with `{"name":"New name"}` | Rename the instance                                            |
| `POST /api/v1/environments/{id}/stop`                        | Stop a supported, ready managed instance while retaining files |
| `DELETE /api/v1/environments/{id}`                           | Destroy a managed instance, or forget an external registration |

Finish active runs and desktop control before stopping or deleting. For deletion, also remove thread mounts. A permanently failed instance is the exception: deletion removes its mounts too. Unsettled operations can block deletion with `operation_unresolved`. Removing a mount alone preserves the instance; deleting a managed instance destroys its files.

## Mount environments on a thread

Use a mount to attach the instance to an existing open thread:

1. Set `THREAD` to the thread ID and `ENVIRONMENT` to the instance ID.
2. Read `GET /api/v1/threads/$THREAD/environments` and save its response ETag as `THREAD_ETAG`, including the quotes.
3. Add the mount. Omit `working_directory` to use the provider's default directory:

```sh
curl -sS -X POST "$A13N_URL/api/v1/threads/$THREAD/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d "{\"name\":\"data\",\"environment_id\":\"$ENVIRONMENT\"}"
```

4. Send a new message asking the agent to list files on `data`. Inspect the selected mount in the tool call. Earlier accepted runs keep their previous mounts.

Mount edits require `run` and the **thread's** ETag. `workspace` appears at `/workspace`; named mounts such as `data` appear at `/mnt/data`. Each thread can mount an instance and a name only once. Remove a mount with `DELETE /api/v1/threads/{thread_id}/environments/{name}` before replacing it. Several threads can share one instance and its files concurrently.

The default mount is `workspace`, or the sole user mount when there is no `workspace`. Multiple named mounts without `workspace` have no default. Before the first model request, Service reads an available default-directory `AGENTS.md` and lists nearby guidance and skill directories for on-demand inspection. This can activate the default mount before a tool call. Attachments and environment-backed skills can also trigger preparation.

`lazy_environment` defaults to `true`. First use creates a reserved instance or starts a stopped one, waiting up to `environments.wait_seconds`. Set it to `false` to prepare every mounted instance before execution; execution connections still open on first dependent use. Override it for one message with:

```json
{"options":{"overrides":{"lazy_environment":false}}}
```

Retries and continuations retain the accepted choice. Forks share mounts unless `fresh_environments` is selected; archiving removes the thread's mounts. Async [subagents](agent-configuration.md#subagents) use their edge's `shared`, `dedicated` or no-environment policy. Inline children share the parent's environment.

### Reuse an instance with a project directory

In a new conversation's **Options**, choose **Reuse existing** and enter an optional **Working directory**. For an existing conversation, use **Environments and memory → Add environment**. Leave the directory empty for the provider default.

Choose an existing path inside the environment. For Local, `/projects/app` means `{template root}/{environment ID}/projects/app`, not a browser-computer path. Service checks the directory on activation and reports missing or inaccessible paths without creating them. The API field is `working_directory` on mount creation.

Relative files, the mount route and command cwd use that directory. Two threads can organize work in different directories on the same instance, but still share processes, ports, installed software and operating-system permissions. For isolation, choose an isolated backend and give each task its own instance.

## Computer Use

<span id="create-a-docker-desktop"></span> <span id="automatic-vercel-desktops"></span> <span id="enable-observation-and-interaction"></span> <span id="computer-coordinate-mode"></span> <span id="desktop-access-prerequisites"></span> <span id="get-and-return-desktop-control"></span> <span id="upgrade-desktop-control"></span> <span id="automatic-sprites-desktops"></span>

- [Work with a desktop](computer-use.md)

## Envd settings

Every environment template has a shared **Envd settings** panel. Configure Device totals and per-Session budgets, file/output sizes, retention and transfer cleanup, operation/startup deadlines, Session lifetime defaults and advanced protocol sizes. Byte inputs use bytes; duration inputs use seconds and save milliseconds. Leave an input empty to inherit its displayed default. The capacity summary identifies the budgets limiting independent Session workers; it is an accounting bound, not a CPU, memory or disk guarantee.

For example, Envd defaults reserve 64 transfer slots per worker Session out of 128 Device slots. Set **Max concurrent file transfers** to `32` to permit more than two workers. Other default reservations still bound admission to four. Raise Device totals, lower per-Session allowances, or combine both to fit the intended workload.

API callers use the same typed recipe object:

```json
{
  "envd": {
    "limits": {
      "max_concurrent_file_transfers": 32
    },
    "idle_timeout_ms": 3600000
  }
}
```

Service injects these overrides through `A13N_ENVD_CONFIG_JSON`. Do not add reserved `A13N_ENVD_*` names to the ordinary Environment variables section. Provider connection, identity, credentials and execution policy retain their own controls; Metrics Data Sources retain monitoring and export configuration. Daemon precedence, from lowest to highest, is built-in defaults, JSON file, JSON environment overlay, individual daemon variables, then explicit CLI arguments.

Settings apply when a new environment is created and stay frozen for create retries and snapshot resume. To change an existing environment, save the template, create a new instance, switch the relevant bindings, and retire the old instance when its files and work are no longer needed. Native execution choices can retain these settings without starting a daemon. Local directory allocation does not start Envd; Sprites applies settings when preparing its Envd launcher. External targets remain configured by their deployment owner.

Session idle timeout and maximum lifetime are daemon defaults used when the Host omits an explicit Session lifecycle. They do not control environment idle stop/deletion or override Thread Session bindings.
