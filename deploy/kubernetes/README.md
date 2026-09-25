# Kubernetes deployment

This directory supplies a local Helm Chart for a13n Service and values for a local kind cluster, AWS EKS and Google GKE. The same Service image runs a `control` Deployment (API, maintenance and delivery), a `worker` Deployment (run execution) and a schema migration Job. A separate Console image serves the browser application and proxies same-origin API traffic. The Chart does not create cloud infrastructure, seed users or models, or provision agent environments. Cloud examples contain placeholders and are not production-ready until the dependencies below are configured and tested.

Helm is the installation/upgrade tool; `a13n-service/` is the Chart (deployment package); an installation such as `a13n` is a Helm release. No published Chart registry or independently released Chart version is assumed. Install from this checkout. `make dev` does not use this Chart.

## Topology

| Input              | Local                                  | AWS                                | GCP                                   |
| ------------------ | -------------------------------------- | ---------------------------------- | ------------------------------------- |
| Values             | `values-local.yaml`                    | `values-aws.yaml`                  | `values-gcp.yaml`                     |
| Service replicas   | One control, one worker                | Two control, two worker            | Two control, two worker               |
| Kubernetes         | kind                                   | EKS                                | GKE                                   |
| PostgreSQL         | Bundled PostgreSQL 17 for development  | External RDS PostgreSQL            | External Cloud SQL PostgreSQL         |
| Redis              | Bundled Redis 7 with AOF and PVC       | External Redis-compatible endpoint | External Memorystore for Redis        |
| Objects            | Local claim shared by all Service Pods | S3 general-purpose bucket          | Verified S3-compatible endpoint       |
| Image distribution | Loaded into the kind node              | ECR or another accessible registry | Artifact Registry or another registry |

`roles.control` and `roles.worker` each configure `replicaCount` and optional `resources` (otherwise inherited from top-level `resources`). These are starting values, not a measured capacity recommendation. The shared Deployments, Services, configuration and probes are portable; cluster provisioning, IAM, image access, CSI drivers, storage classes and ingress controllers remain platform-specific. Use an image built for the node architecture.

For release `a13n`, the Deployments are `a13n-a13n-control` and `a13n-a13n-worker`. The ClusterIP Service `a13n-a13n-control` serves the API; workers accept no traffic. The Console proxies `/api` and `/readyz` to Control, and Ingress sends the configured paths to the Console, or directly to Control with `console.enabled=false`. All Service Pods share the database, Redis and object storage.

### Schema migration

Every install and upgrade creates the Job `a13n-a13n-migrate-<revision>`, which runs `a13n-service migrate` under the Service's PostgreSQL advisory lock. Replicas never migrate: the Chart configures `auto_migrate = false`, and each Control and Worker Pod first runs a `wait-for-schema` init container that repeats `a13n-service migrate --check` every five seconds until the database matches its image. Rolling updates keep old Pods serving until new ones are ready, so review schema compatibility before upgrading: old replicas remain active during migration. A Helm rollback rolls back Kubernetes resources, not the database schema. Finished Jobs are deleted after one day.

### Objects

With `objects.backend: local`, every Service Pod mounts the claim `a13n-a13n-objects` at `/app/var/objects`. The Chart's claim is ReadWriteOnce, which shares it only among Pods on one node; kind has one node. On multi-node clusters set `persistence.existingClaim` to a ReadWriteMany claim (adapt `examples/objects-pvc.yaml`) or use `objects.backend: s3`. The local claim has Helm's `keep` policy. Switching backends does not migrate stored objects.

### Metrics

Control and Worker Pods serve Prometheus metrics at `/metrics` on the container port `metrics` (`metrics.port`, 9464 by default), set as `A13N_TELEMETRY__METRICS_PORT` so a `[telemetry]` section in `extraConfig` still applies. No Service or Ingress exposes that port. The Pods carry `prometheus.io` scrape annotations, and `metrics.podMonitor.enabled` adds a PodMonitor for the Prometheus or VictoriaMetrics operator, carrying `metrics.podMonitor.labels`. `metrics.enabled: false` turns metrics off. The [monitoring bundle](../monitoring/README.md) holds alert rules and dashboards for these metrics.

## Configuration and secrets

The Chart renders `service.toml` from values: `[server]` (including `publicUrl` and `trustedProxies`), `[database]`, `[objects]` and, with bundled Redis, `[redis]`. `extraConfig` appends other non-secret sections such as `[providers]` or `[telemetry]`. The Secret named by `existingSecret` supplies credentials as Service settings, `A13N_<SECTION>__<FIELD>`, which override the file. Unknown `A13N_` variables stop the Service, so Pods disable Kubernetes service-link variables. See the [configuration reference](../../docs/a13n-service/configuration-reference.md).

`publicUrl` is the origin browsers use; the Service accepts browser state changes only from it and always issues `Secure` session cookies, so use HTTPS except on `127.0.0.1`. Rate limits key on client addresses: set `trustedProxies` to the addresses or CIDRs of the Console Pods and front proxies so forwarded client addresses are used.

Run commands from the repository root. Helm and kubectl must point to the intended cluster. The examples use release `a13n` in namespace `a13n-service`; the bundled PostgreSQL host is `a13n-a13n-postgres`.

1. Copy `examples/service.env.example` and, for bundled PostgreSQL, `examples/postgres.env.example` to protected files outside the checkout. Set permissions to `600`.
2. Replace all placeholders. Generate the encryption key using `openssl rand -base64 32`. Preserve the key ring across restarts and upgrades; losing it makes stored provider and connection credentials unreadable.
3. Match the PostgreSQL password in both files. URL-encode it in the Service URL. Changing the PostgreSQL Secret does not change the password of an already initialized database.
4. For AWS/GCP, replace the database URL, add `A13N_REDIS__URL`, and configure object-store credentials unless the ServiceAccount's cloud identity provides them. Use a non-cluster Redis endpoint and the database/Redis provider's required TLS settings.
5. Create the namespace and Secrets. These commands change the selected cluster:

```sh
kubectl config current-context
kubectl create namespace a13n-service
kubectl -n a13n-service create secret generic a13n-service-secrets \
  --from-env-file=/absolute/private/path/service.env
kubectl -n a13n-service create secret generic a13n-postgres-secrets \
  --from-env-file=/absolute/private/path/postgres.env
```

Secrets must exist in the release namespace. Do not put secrets in values, `extraConfig`, shell command arguments, or committed manifests. Existing Secrets are not managed or hashed by Helm; restart both Deployments after a Secret update. Configuration changes roll Pods through a configuration checksum.

After the first installation, create the first administrator. The command prompts for a password of at least 12 characters and refuses once the Service is initialized:

```sh
kubectl -n a13n-service exec -it deployment/a13n-a13n-control -- \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

## Local installation with Console

### One-command startup

With Docker running and `python3`, kind, kubectl and Helm installed, run from the repository root:

```sh
make k8s-up
```

The launcher creates or reuses the `a13n-local` kind cluster, checks its loopback port mapping, prepares Secrets in namespace `a13n-service`, builds and loads both images, waits for the migration and the Helm release, and creates the first administrator. Each build uses a new local image tag so deployments pick up rebuilt images. This command owns the local values; use the manual workflow below for customized deployments. All checkouts on the same machine share this named cluster and port.

On a fresh installation it generates a random PostgreSQL password and encryption key, saves `service.env` and `postgres.env` under `~/.config/a13n-service-kind` with mode `600`, and creates the corresponding Secrets. It also generates the administrator password into `admin.env` in the same directory and bootstraps `admin@example.com`; set `K8S_ADMIN_EMAIL=you@example.com make k8s-up` to choose another email. `K8S_STATE_DIR` may select another protected directory outside the checkout. Existing local files and cluster Secrets are reused and must agree; the launcher refuses invalid keys, conflicting credentials, or new credential generation when PVCs already exist. Existing cluster Secrets can restore missing local files. Later runs keep the initialized administrator and its password.

The terminal then prints the Console URL, <http://127.0.0.1:8080>, and where the administrator password is stored; it never prints the password. If existing data has no local administrator record, the launcher prints the manual bootstrap command instead. The launcher does not delete clusters, PVCs or existing Secrets. Node port 30080 must be free: uninstall any other release that holds it before `make k8s-up`. `make k8s-check` runs the offline launcher and Chart tests and lints the local values without building images or changing a cluster.

### Manual startup

This recipe creates a kind cluster using the running Docker Engine. The included `kind-local.yaml` maps host `127.0.0.1:8080` to node port `30080`; `values-local.yaml` exposes the Console on that node port and configures the same public URL. No foreground port-forward is required.

On first use:

```sh
kind create cluster --name a13n-local --config deploy/kubernetes/kind-local.yaml --wait 5m
kubectl --context kind-a13n-local create namespace a13n-service
```

Reuse a cluster created with this exact port mapping. An existing cluster without the mapping cannot acquire it through Helm; create a separately named cluster rather than deleting existing data. If host port 8080 is occupied, select a free host port in the kind configuration before cluster creation and set `publicUrl` to match.

Build both images and load them into the cluster (repeat after source changes):

```sh
make image-a13n-service image-a13n-console
kind load docker-image a13n-service:local a13n-console:local --name a13n-local
```

The Console image builds the frontend with Node.js 24 and the workspace-pinned pnpm version. It installs only the root, Console and shared UI workspace dependencies before copying source files, so source-only changes reuse the dependency layer; the first build needs registry access. The runtime is non-root Nginx with a read-only root filesystem and writable `/tmp`. It serves browser routes, returns 404 for missing assets, and forwards API errors and server-sent events unbuffered. Proxy access logs exclude query strings, and request bodies are capped at the Service's largest configurable request size.

Create the two Secrets described above in this cluster, then install or update the release:

```sh
helm upgrade --install a13n deploy/kubernetes/a13n-service \
  --kube-context kind-a13n-local --namespace a13n-service \
  -f deploy/kubernetes/values-local.yaml --wait --timeout 20m
kubectl --context kind-a13n-local -n a13n-service get pods,jobs,pvc
```

Create the administrator with the bootstrap command above (add `--context kind-a13n-local`), open <http://127.0.0.1:8080> and sign in. <http://127.0.0.1:8080/readyz> checks Control readiness and `/healthz` the Console proxy itself. If rebuilding with the same `local` tags, Helm does not detect image-content changes: restart both Service Deployments and the Console after loading the images. Using a new tag for each build and setting both image tags on upgrade avoids this.

The local values enable single-replica Redis 7 and PostgreSQL 17 StatefulSets for trusted development only; Redis is unauthenticated, uses AOF with `appendfsync everysec`, and has no host port or Ingress. Closing the terminal does not stop the Pods or the port mapping. Claims survive Pod replacement and uninstallation, not deletion of the kind cluster.

## AWS and GCP

Copy the relevant values file outside the repository and replace image, bucket, endpoint, network, domain and certificate placeholders. Prepare:

- A cluster with sufficient capacity, network access to its dependencies and image-pull permissions. Validate cluster-specific admission and resource policies.
- PostgreSQL and a reachable Redis endpoint. This Chart does not create RDS, Cloud SQL, ElastiCache or Memorystore. The GCP example assumes a reachable private database endpoint; Cloud SQL Auth Proxy is not included.
- A compatible bucket with permissions for reading, listing and conditional create-only writes. AWS should use EKS Pod Identity or IRSA for the generated ServiceAccount (`a13n-a13n` for release `a13n`); set `serviceAccount.annotations` for IRSA and configure the trust relationship. Default service-account token automount is disabled.
- An ingress controller, DNS and TLS configuration matching `publicUrl`, and `trustedProxies` covering the load balancer and Console Pod addresses. Ingress is disabled until these are prepared. The AWS values target AWS Load Balancer Controller with an ACM certificate. The GCP values target GKE Ingress with a TLS Secret; apply `examples/gcp-backendconfig.yaml` and `examples/gcp-console-backendconfig.yaml` in the release namespace first. They select `/readyz` and `/healthz` health checks and a one-hour backend timeout; validate it with long-lived server-sent event streams.

GCS is not a supported drop-in substitution for the S3 endpoint. Supply a verified S3-compatible service, or implement and validate a native GCS adapter separately. See the [configuration guide](../../docs/a13n-service/configuration.md).

```sh
helm upgrade --install a13n deploy/kubernetes/a13n-service \
  --kube-context YOUR_CLOUD_CONTEXT --namespace YOUR_NAMESPACE \
  -f /absolute/private/path/values-cloud.yaml --wait --timeout 20m
```

The cloud values run two Console replicas from a separate image repository; build and push both images, then fill both image configurations. The Console Deployment mounts no application credentials or Service storage.

## Agent execution environments

The Chart creates no per-agent Pods, Docker daemon, privileged container or Docker socket mount. Hosted sandbox providers (E2B, Daytona, Modal, Vercel, Sprites, Runloop) need outbound access from the Worker Pods to their vendor APIs; external envd targets need network access from the Worker Pods to their endpoints. Running Envd targets or managing Kubernetes sandboxes is a separate deployment concern.

## Validation and operations

These commands render locally and do not connect to a cluster:

```sh
make k8s-check
helm lint deploy/kubernetes/a13n-service -f deploy/kubernetes/values-aws.yaml --strict
helm lint deploy/kubernetes/a13n-service -f deploy/kubernetes/values-gcp.yaml --strict
helm template a13n deploy/kubernetes/a13n-service -f deploy/kubernetes/values-local.yaml
```

Templates use `.tpl` because they contain Go template syntax rather than standalone YAML. Rendering does not verify image availability, Kubernetes admission, credentials, storage semantics, database connectivity or rollout behavior.

Uninstalling does not delete the retained objects claim, the PostgreSQL and Redis StatefulSet claims, or external resources. Record retained claim names before uninstalling; `persistence.existingClaim` reuses an objects claim in a later installation. Do not delete PVCs to resolve startup failures. Back up PostgreSQL, objects and the encryption key ring independently.
