# Kubernetes deployment

`helm/` holds the Helm Chart for a13n Service, `helm/a13n-service/`, and its values for a local kind cluster, `helm/values-local.yaml`; `kind-local.yaml` configures that cluster, and `examples/` holds Secret templates and a shared objects claim. The same Service image runs a `control` Deployment (API, Console, maintenance and delivery), a `worker` Deployment (run execution) and a schema migration Job. The Chart does not provision external infrastructure, seed users or models, or provision agent environments.

Each Service release publishes a Helm chart at `oci://ghcr.io/converge-ai-labs/charts/a13n-service` that selects the matching image. The checkout chart selects `ghcr.io/converge-ai-labs/a13n-service:dev` unless you override it. `make dev` uses a separate local stack.

For an Alibaba Cloud ACS cluster, start with the [values example](examples/values-acs.example.yaml) and [Service settings example](examples/service-acs.env.example). The workload Chart is shared with other clusters; the kind values and launcher are local-only.

## Topology

| Input              | Local kind cluster                     | Other clusters                                      |
| ------------------ | -------------------------------------- | --------------------------------------------------- |
| Values             | `helm/values-local.yaml`               | Your own values file                                |
| Service replicas   | One control, one worker                | `roles.<role>.replicaCount` or `autoscaling`        |
| PostgreSQL         | Bundled PostgreSQL 17 for development  | External PostgreSQL                                 |
| Redis              | Bundled Redis 7 with AOF and PVC       | External Redis                                      |
| Objects            | Local claim shared by all Service Pods | S3-compatible bucket or a ReadWriteMany claim       |
| Image distribution | Loaded into the kind node              | The published image or a registry the nodes can use |
| Exposure           | Node port 30080 on `127.0.0.1:8080`    | Ingress with TLS                                    |

`roles.control` and `roles.worker` each configure `replicaCount` and optional `resources` (otherwise inherited from top-level `resources`). These are starting values, not a measured capacity recommendation. With `autoscaling.enabled`, a role's HorizontalPodAutoscaler replaces `replicaCount`: it keeps the role between `minReplicas` and `maxReplicas` near `targetCPUUtilizationPercentage` of its CPU request, which needs the cluster's metrics server and a CPU request on the role. Worker CPU follows run processing but not runs waiting on model responses, and each worker runs at most `worker.slots` runs at once, so set the worker `minReplicas` from the expected concurrent runs. Each role's PodDisruptionBudget lets voluntary disruptions, such as node drains, evict one of its Pods at a time. The shared Deployments, Services, configuration and probes are portable; cluster provisioning, IAM, image access, CSI drivers, storage classes and ingress controllers remain platform-specific. Use an image built for the node architecture.

For release `a13n`, the Deployments are `a13n-a13n-control` and `a13n-a13n-worker`. The Service `a13n-a13n-control` serves the API and the Console from one origin; workers accept no traffic. Ingress sends the configured paths to it; `service.type` and `service.nodePort` expose it without Ingress, as on kind. All Service Pods share the database, Redis and object storage. Service containers run as UID 10001 with a read-only root filesystem, no capabilities and no privilege escalation; they write only to a per-Pod `/tmp` emptyDir and, with local objects, the objects claim.

### Shutdown time

`terminationGracePeriodSeconds` sets the Pod termination grace period for both roles and defaults to `60`. Service first stops accepting HTTP connections and waits up to `server.shutdown_timeout` for existing requests. It then gives background tasks another `server.shutdown_timeout` budget, including the worker's `worker.drain_seconds` handoff, before closing clients and flushing telemetry. Size the Pod grace period for the entire sequence, not just the worker handoff. The [runtime contract](../../spec/a13n-service/09-runtime.md#startup-readiness-and-shutdown) owns these stages.

The default Service settings use a 15-second shutdown budget and a 10-second worker handoff. If an environment raises them, raise the Helm value as well; for example, use a 300-second Pod grace period as a starting point for a 120-second shutdown budget and a 90-second handoff, then verify the actual shutdown under load. Helm cannot inspect settings supplied by `existingSecret` and does not validate their timing against the Pod grace period. A process that finishes early exits immediately; increasing the limit only extends how long Kubernetes waits before forcing termination. Changing the Helm value rolls the Deployments.

### Runtime ServiceAccount

By default the Chart creates a ServiceAccount named after the release. `serviceAccount.name` can choose a different name. To use an administrator-provisioned account, set `serviceAccount.create: false` and supply its name; the control and worker Pods, their init containers and the migration Job all use it. Configure annotations on an external account through its owner, leaving `serviceAccount.annotations` empty. Service Pods disable automatic API-token mounting even when the external account enables it; workload-identity admission must provide its own required credential projection.

The runtime ServiceAccount is separate from the identity running Helm. When moving an existing release to an external account, provision a new account name first and switch references deliberately. Simply setting `create: false` removes the previously Chart-owned account from the release; it does not transfer ownership or preserve that account.

### Service metadata

`service.labels` and `service.annotations` customize only the control Service's metadata. Labels do not change Deployment selectors, Pod labels, resource names or Secret selection. Configure platform-specific integrations in an environment-owned values file according to that platform's contract.

### Schema migration

Every install and upgrade creates the Job `a13n-a13n-migrate-<revision>`, which runs `a13n-service migrate` under the Service's PostgreSQL advisory lock. Replicas never migrate: the Chart configures `auto_migrate = false`, and each Control and Worker Pod first runs a `wait-for-schema` init container that repeats `a13n-service migrate --check` every five seconds until the database matches its image. Rolling updates keep old Pods serving until new ones are ready, so review schema compatibility before upgrading: old replicas remain active during migration. A Helm rollback rolls back Kubernetes resources, not the database schema. Finished Jobs are deleted after one day.

### Objects

With `objects.backend: local`, every Service Pod mounts the claim `a13n-a13n-objects` at `/app/var/objects`. The Chart's claim is ReadWriteOnce, which shares it only among Pods on one node; kind has one node. On multi-node clusters set `persistence.existingClaim` to a ReadWriteMany claim (adapt `examples/objects-pvc.yaml`) or use `objects.backend: s3`. The local claim has Helm's `keep` policy. Switching backends does not migrate stored objects.

### Metrics

Control and Worker Pods serve Prometheus metrics at `/metrics` on the container port `metrics` (`metrics.port`, 9464 by default), set as `A13N_TELEMETRY__METRICS_PORT` so a `[telemetry]` section in `extraConfig` still applies. No Service or Ingress exposes that port. The Pods carry `prometheus.io` scrape annotations, and `metrics.podMonitor.enabled` adds a PodMonitor for the Prometheus or VictoriaMetrics operator, carrying `metrics.podMonitor.labels`. `metrics.enabled: false` turns metrics off. The [monitoring bundle](../monitoring/README.md) holds alert rules and dashboards for these metrics.

## Configuration and secrets

The Chart renders `service.toml` from values: `[server]` (including `publicUrl` and `trustedProxies`), `[database]`, `[objects]` and, with bundled Redis, `[redis]`. `extraConfig` appends other non-secret sections such as `[providers]` or `[telemetry]`. The Secret named by `existingSecret` supplies credentials as Service settings, `A13N_<SECTION>__<FIELD>`, which override the file. Unknown `A13N_` variables stop the Service, so Pods disable Kubernetes service-link variables. See the [configuration reference](../../docs/a13n-service/configuration-reference.md).

`publicUrl` is the origin browsers use; the Service accepts browser state changes only from it, and marks its cookies `Secure` only when it is HTTPS, so use HTTPS wherever the Service is reachable beyond a trusted network. Rate limits key on client addresses: set `trustedProxies` to the addresses or CIDRs of the front proxies so forwarded client addresses are used.

Run commands from the repository root. Helm and kubectl must point to the intended cluster. The examples use release `a13n` in namespace `a13n-service`; the bundled PostgreSQL host is `a13n-a13n-postgres`.

1. Copy `examples/service.env.example` and, for bundled PostgreSQL, `examples/postgres.env.example` to protected files outside the checkout. Set permissions to `600`.
1. Replace all placeholders. Generate the encryption key using `openssl rand -base64 32`. Preserve the key ring across restarts and upgrades; losing it makes stored provider and connection credentials unreadable.
1. Match the PostgreSQL password in both files. URL-encode it in the Service URL. Changing the PostgreSQL Secret does not change the password of an already initialized database.
1. For external stores, replace the database URL, add `A13N_REDIS__URL`, and add object-store credentials unless the Pods' identity provides them. Use the TLS settings the database and Redis require.
1. Create the namespace and Secrets. These commands change the selected cluster:

```sh
kubectl config current-context
kubectl create namespace a13n-service
kubectl -n a13n-service create secret generic a13n-service-secrets \
  --from-env-file=/absolute/private/path/service.env
kubectl -n a13n-service create secret generic a13n-postgres-secrets \
  --from-env-file=/absolute/private/path/postgres.env
```

Secrets must exist in the release namespace. Do not put secrets in values, `extraConfig`, shell command arguments, or committed manifests. Existing Secrets are not managed or hashed by Helm; restart both Deployments after a Secret update. Configuration changes roll Pods through a configuration checksum.

After the first installation, create the first administrator. Console offers this to its first visitor on an uninitialized Service, so when the Ingress is reachable by others, run the command instead before opening it. The command prompts for a password of at least 12 characters and refuses once the Service is initialized:

```sh
kubectl -n a13n-service exec -it deployment/a13n-a13n-control -- \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

## Local installation

### One-command startup

With Docker running and `python3`, kind, kubectl and Helm installed, run from the repository root:

```sh
make k8s-up
```

The launcher creates or reuses the `a13n-local` kind cluster, checks its loopback port mapping, prepares Secrets in namespace `a13n-service`, builds and loads the image, waits for the migration and the Helm release, and creates the first administrator. Each build uses a new local image tag so deployments pick up rebuilt images. This command owns the local values; use the manual workflow below for customized deployments. All checkouts on the same machine share this named cluster and port.

On a fresh installation it generates a random PostgreSQL password and encryption key, saves `service.env` and `postgres.env` under `~/.config/a13n-service-kind` with mode `600`, and creates the corresponding Secrets. It also generates the administrator password into `admin.env` in the same directory and bootstraps `admin@example.com`; set `K8S_ADMIN_EMAIL=you@example.com make k8s-up` to choose another email. `K8S_STATE_DIR` may select another protected directory outside the checkout. Existing local files and cluster Secrets are reused and must agree; the launcher refuses invalid keys, conflicting credentials, or new credential generation when PVCs already exist. Existing cluster Secrets can restore missing local files. Later runs keep the initialized administrator and its password.

The terminal then prints the Console URL, <http://127.0.0.1:8080>, and where the administrator password is stored; it never prints the password. If existing data has no local administrator record, the launcher prints the manual bootstrap command instead. The launcher does not delete clusters, PVCs or existing Secrets. Node port 30080 must be free: uninstall any other release that holds it before `make k8s-up`. `make k8s-smoke` then checks the running deployment the way its administrator uses it: Console loads, the administrator signs in and a credential can be stored. `make k8s-check` runs the offline launcher and Chart tests and lints the Chart with its default and local values without building images or changing a cluster. Deployment CI runs both on a fresh kind cluster, then repeats `make k8s-up` to upgrade the release through a new migration Job.

### Manual startup

This recipe creates a kind cluster using the running Docker Engine. The included `kind-local.yaml` maps host `127.0.0.1:8080` to node port `30080`; `helm/values-local.yaml` exposes the control Service on that node port and configures the same public URL. No foreground port-forward is required.

On first use:

```sh
kind create cluster --name a13n-local --config deploy/kubernetes/kind-local.yaml --wait 5m
kubectl --context kind-a13n-local create namespace a13n-service
```

Reuse a cluster created with this exact port mapping. An existing cluster without the mapping cannot acquire it through Helm; create a separately named cluster rather than deleting existing data. If host port 8080 is occupied, select a free host port in the kind configuration before cluster creation and set `publicUrl` to match.

Build the image and load it into the cluster (repeat after source changes):

```sh
make image-a13n-service
kind load docker-image a13n-service:local --name a13n-local
```

The image build includes Console; the first build needs dependency registry access.

Create the two Secrets described above in this cluster, then install or update the release:

```sh
helm upgrade --install a13n deploy/kubernetes/helm/a13n-service \
  --kube-context kind-a13n-local --namespace a13n-service \
  -f deploy/kubernetes/helm/values-local.yaml --wait --timeout 20m
kubectl --context kind-a13n-local -n a13n-service get pods,jobs,pvc
```

Create the administrator with the bootstrap command above (add `--context kind-a13n-local`), open <http://127.0.0.1:8080> and sign in. <http://127.0.0.1:8080/readyz> checks Control readiness. If rebuilding with the same `local` tag, Helm does not detect image-content changes: restart both Service Deployments after loading the image. Using a new tag for each build and setting the image tag on upgrade avoids this.

The local values run development-only PostgreSQL and Redis. Redis has no authentication or external exposure, so keep this cluster local. Closing the terminal does not stop the Pods. Claims survive Pod replacement and uninstallation, but not deletion of the kind cluster.

## Other clusters

Write a values file outside the repository, starting from `helm/a13n-service/values.yaml`, and prepare:

- A cluster with sufficient capacity, network access to its dependencies and image-pull access. Validate the cluster's admission and resource policies.
- PostgreSQL and a reachable Redis endpoint. The bundled ones (`postgresql.enabled`, `redis.enabled`) are for development only.
- Objects: an S3-compatible bucket with permissions for reading, listing and conditional create-only writes, or a ReadWriteMany claim through `persistence.existingClaim`. `serviceAccount.annotations` can attach a workload identity to the generated ServiceAccount (`a13n-a13n` for release `a13n`); default service-account token automount is disabled.
- An ingress controller, DNS and TLS matching `publicUrl`, and `trustedProxies` covering the proxy addresses. Health-check `/readyz`, and allow long-lived server-sent event streams through the proxy's idle timeout.

Install a Service release with its published Chart, which deploys that release's image:

```sh
helm upgrade --install a13n oci://ghcr.io/converge-ai-labs/charts/a13n-service --version VERSION \
  --kube-context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
  -f /absolute/private/path/values.yaml --wait --timeout 20m
```

To install the Chart of this checkout instead, build and push the image, set `image.repository` and either `image.tag` or `image.digest`, and pass `deploy/kubernetes/helm/a13n-service` in place of the OCI reference. When set, `image.digest` takes precedence over the tag and pins every Service role and migration container to the same artifact.

## Agent execution environments

The Chart creates no per-agent Pods, Docker daemon, privileged container or Docker socket mount. Hosted sandbox providers (E2B, Daytona, Modal, Vercel, Sprites, Runloop) need outbound access from the Worker Pods to their vendor APIs; external envd targets need network access from the Worker Pods to their endpoints. Running Envd targets or managing Kubernetes sandboxes is a separate deployment concern.

## Upgrades and backups

Back up before upgrading. Upgrade the release to another published Chart version with the same values file:

```sh
helm upgrade a13n oci://ghcr.io/converge-ai-labs/charts/a13n-service --version NEW_VERSION \
  --kube-context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
  -f /absolute/private/path/values.yaml --wait --timeout 20m
```

The upgrade runs its migration Job while the previous Pods keep serving, then rolls the Deployments ([schema migration](#schema-migration)). If the Job fails, the new Pods never become ready, Helm reports the upgrade as failed at its timeout and the previous Pods keep serving; read the logs of `job/a13n-a13n-migrate-<revision>`. `helm rollback` restores Kubernetes resources only. A migrated schema stops the previous image at startup, so returning to it means restoring the database backup taken before the upgrade.

Back up three things together and restore them from the same point in time:

- PostgreSQL, through the provider's snapshots or point-in-time recovery, or `pg_dump`.
- Objects: bucket versioning or replication for `s3`, or a volume snapshot of the objects claim for `local`.
- The Secret named by `existingSecret`. Its encryption key ring decrypts stored provider and connection credentials; keep a copy outside the cluster.

## Validation and operations

These commands render locally and do not connect to a cluster:

```sh
make k8s-check
helm template a13n deploy/kubernetes/helm/a13n-service -f deploy/kubernetes/helm/values-local.yaml
```

Rendering checks template output, not image availability, credentials, storage or rollout behavior.

Uninstalling does not delete the retained objects claim, the PostgreSQL and Redis StatefulSet claims, or external resources. Record retained claim names before uninstalling; `persistence.existingClaim` reuses an objects claim in a later installation. Do not delete PVCs to resolve startup failures.
