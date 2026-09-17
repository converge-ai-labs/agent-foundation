# Kubernetes deployment

This directory supplies a local Helm Chart for a13n Service and values examples for Docker Desktop, AWS EKS and Google GKE. Local deployment runs the shared Service image in `all` mode. Distributed deployment runs the same image as three independent Deployments: `control`, `worker` and `connectivity`. A separate Console image serves the browser application and proxies same-origin API and WebSocket traffic. The Chart does not create cloud infrastructure, seed users or Models, or provision Agent sandboxes. Cloud examples contain placeholders and are not production-ready until the dependencies below are configured and tested.

Helm is the installation/upgrade tool; `a13n-service/` is the Chart (deployment package); an installation such as `a13n` is a Helm release. No published Chart registry or independently released Chart version is assumed. Install from this checkout.

## Profiles

| Input              | Local                                 | AWS                                              | GCP                                              |
| ------------------ | ------------------------------------- | ------------------------------------------------ | ------------------------------------------------ |
| Values             | `values-local.yaml`                   | `values-aws.yaml`                                | `values-gcp.yaml`                                |
| Service processes  | One `all` Pod, Recreate updates       | Separate Control/Worker/Connectivity Deployments | Separate Control/Worker/Connectivity Deployments |
| Kubernetes         | Docker Desktop                        | EKS                                              | GKE                                              |
| PostgreSQL         | Bundled PostgreSQL 17 for development | External RDS PostgreSQL                          | External Cloud SQL PostgreSQL                    |
| Redis              | Bundled Redis 7 with AOF and PVC      | External Redis-compatible endpoint               | External Memorystore for Redis                   |
| Objects            | Local PVC directory                   | S3 general-purpose bucket                        | Verified S3-compatible endpoint                  |
| Filesystem         | Same PVC, separate directory          | Existing EFS RWX claim                           | Existing Filestore RWX claim                     |
| Image distribution | Import into the node image store      | ECR or another accessible registry               | Artifact Registry or another accessible registry |

The shared Deployment, Service, configuration and probes are portable. Cluster provisioning, IAM, image access, CSI drivers, storage classes and ingress controllers remain platform-specific. Use an image built for the node architecture; the local Mac image may be ARM64 while cloud nodes may be AMD64.

In the distributed profile, `roles.control`, `roles.worker` and `roles.connectivity` each configure `replicaCount` and optional `resources` (otherwise inherited from top-level `resources`). Both cloud examples start two replicas per role, six Service Pods in total. These are starting values, not a measured capacity recommendation. Top-level `replicaCount` applies only to local `all` mode.

For release `a13n`, the cloud Deployments and ClusterIP Services are named `a13n-a13n-control`, `a13n-a13n-worker` and `a13n-a13n-connectivity`. Role labels keep Service selectors disjoint. The Control Service serves APIs, the Connectivity Service accepts `/connectivity/v1` provider event traffic, and the Worker Service exposes operational probes only. Ingress explicitly sends `/connectivity/v1` to Connectivity and the configured remaining paths to Console. Console proxies API, gateway and authorization-bridge requests to Control and provider event requests to Connectivity. With `console.enabled=false`, the remaining ingress paths go directly to Control. Worker has no public ingress. Connectivity management APIs remain on Control. All three roles share the database, Redis, object storage and configured filesystem, and use the same public origin.

Only Control (or local `all`) enables automatic migration, through an explicit container environment setting. Worker and Connectivity disable it and use a read-only schema-check init container before starting; they never migrate. Each init container retries the schema check up to 420 times with five-second sleeps, then exits with an error for Kubernetes to retry. Database operation timeouts add to this wait. Control migrations use the Service PostgreSQL advisory lock; its startup probe permits 35 minutes for lock waiting and migrations. Inspect Control logs if schema preparation fails. Review schema compatibility before rolling an upgrade: old replicas may still be active during migration. A Helm rollback rolls back Kubernetes resources, not the database schema.

Local storage requires exactly one Service process, including during updates. The Chart rejects local replicas above one and uses `Recreate`. Never point two local releases at the same object directory. Switching profiles does not migrate stored objects or PostgreSQL data: provision a separate deployment and plan data migration first.

## Prepare configuration and secrets

Run commands from the repository root. Helm and kubectl must point to the intended cluster. The examples use release `a13n` in namespace `a13n-dev`; generated Service and PostgreSQL names are `a13n-a13n` and `a13n-a13n-postgres`.

1. Copy `examples/service.env.example` and, for local PostgreSQL, `examples/postgres.env.example` to protected files outside the checkout. Set permissions to `600`.
2. Replace all placeholders. Generate the master key using `openssl rand -base64 32`. Preserve this key across restarts and upgrades; losing it makes encrypted Provider credentials unreadable.
3. Match the PostgreSQL password in both files. URL-encode it in the Service URL. Changing the PostgreSQL Secret does not change the password of an already initialized database.
4. For AWS/GCP, replace the database URL, enable `A13N_SERVICE_REDIS_URL`, and configure object-store credentials. Use a non-cluster Redis endpoint; this example does not establish Redis Cluster compatibility. Use the database/Redis provider's required TLS settings.
5. Create the namespace and Secrets. These commands change the selected cluster:

```sh
kubectl config current-context
kubectl create namespace a13n-dev
kubectl -n a13n-dev create secret generic a13n-service-secrets \
  --from-env-file=/absolute/private/path/service.env
kubectl -n a13n-dev create secret generic a13n-postgres-secrets \
  --from-env-file=/absolute/private/path/postgres.env
```

The PostgreSQL Secret is unnecessary when `postgresql.enabled=false`. Secrets must exist in the release namespace. Do not put secrets in values, `extraConfig`, shell command arguments, or committed manifests. The Service Secret supplies credential settings only: do not override the Chart's role, deployment mode, backend types, host, port, paths, migration or drain settings. Such overrides can invalidate the Chart's safety checks. Existing Secrets are not managed or hashed by Helm; restart the Deployment after a Secret update. Configure cloud Secret synchronization separately if desired.

## Existing `make dev` workflow

`make dev` does not use this Chart or run Service in Docker. The launcher starts a host Python Service process with `role = "all"` (Control, Worker and Connectivity together), a host scripted-model process, and a separate host Node/Vite Console process. Docker Compose runs PostgreSQL 17 and Redis 7 with persistent volumes. Setup also starts the shared Langfuse stack and optional configured Mem0, then applies schema migrations before starting the applications. Local objects and files live beneath the checkout's resolved development state directory. Use `make dev-status` to discover checkout-specific ports; committed example ports are not the assigned ports. See [the development guide](../../dev/service/README.md).

The local Kubernetes overlay is a separate deployment: Service, PostgreSQL and Redis run inside Kubernetes Pods. The Chart automatically configures Service to connect to the Redis Service; no host Redis installation or startup is needed. It does not change `make dev`, whose Redis is a real Docker container.

## Local Redis

`values-local.yaml` enables a single Redis 7 StatefulSet and an internal Service. For release `a13n`, Service connects to `redis://a13n-a13n-redis:6379/0`. Redis uses a 5 GiB persistent claim and AOF with `appendfsync everysec`; a crash can lose the most recent writes before fsync. Pod replacement reuses its claim. The StatefulSet claim is retained on uninstall by default.

This Redis is unauthenticated and intended only for a trusted local development cluster. It exposes no host port, LoadBalancer or Ingress, but other cluster workloads can reach it. Cloud profiles reject bundled Redis and continue using the protected external endpoint from `A13N_SERVICE_REDIS_URL`. With `redis.enabled=false`, local deployments also require that external URL; there is no in-memory fallback. When bundled Redis is enabled, omit `A13N_SERVICE_REDIS_URL` from the Service Secret so it does not override the generated cluster address. Redis startup may briefly cause Service to restart until the dependency becomes ready.

Adding Redis does not remove the local single-process constraint: the local object backend still requires one Service replica. Existing in-memory Redis state is not migrated when upgrading an older local deployment.

## Local installation with Console

This recipe creates a kind cluster using Docker Desktop's running Docker Engine. It does not require Docker Desktop's built-in Kubernetes cluster. The included `kind-local.yaml` maps host `127.0.0.1:8080` to node port `30080`; `values-local.yaml` exposes the Console on that NodePort and configures the same public origin. No foreground port-forward or host Vite process is required.

Run from the repository root. Install kind if needed (`brew install kind`); Helm and kubectl must also be installed. On first use:

```sh
kind create cluster --name a13n-local --config deploy/kubernetes/kind-local.yaml --wait 5m
kubectl --context kind-a13n-local create namespace a13n-dev
```

Reuse a cluster created with this exact port mapping. An existing cluster without the mapping cannot acquire it through Helm; create this separately named cluster rather than deleting existing data. If host port 8080 is occupied, select a free host port in the kind configuration before cluster creation and set `publicOrigin` to match. Keep the node port at 30080 unless both configurations are updated.

Build both images and load them into the cluster (repeat after source changes):

```sh
docker build -f deploy/containers/a13n-service/Dockerfile -t a13n-service:local .
docker build -f deploy/containers/a13n-console/Dockerfile -t a13n-console:local .
kind load docker-image a13n-service:local a13n-console:local --name a13n-local
```

The Console image builds the frontend with Node.js 24 and the workspace-pinned pnpm version. Runtime uses non-root Nginx with a read-only root filesystem and writable `/tmp`. It serves SPA browser routes, returns 404 for missing assets, and forwards API errors without substituting HTML. WebSockets and unbuffered SSE share the browser origin. Proxy access logs exclude query strings. Uploads have a 128 MiB proxy ceiling; Service retains its own limits.

Prepare the two protected files described above, then create the Secrets in this cluster on first installation:

```sh
kubectl --context kind-a13n-local -n a13n-dev create secret generic a13n-service-secrets \
  --from-env-file=/absolute/private/path/service.env
kubectl --context kind-a13n-local -n a13n-dev create secret generic a13n-postgres-secrets \
  --from-env-file=/absolute/private/path/postgres.env
```

Do not regenerate credentials when upgrading an existing installation. Install or update the release:

```sh
helm upgrade --install a13n deploy/kubernetes/a13n-service \
  --kube-context kind-a13n-local --namespace a13n-dev \
  -f deploy/kubernetes/values-local.yaml --wait --timeout 40m
kubectl --context kind-a13n-local -n a13n-dev get pods,pvc
```

Open <http://127.0.0.1:8080>. API documentation is at <http://127.0.0.1:8080/api/docs>, and <http://127.0.0.1:8080/readyz> checks backend readiness. `/healthz` checks the Console proxy itself. First-time administrator initialization prints a one-time link in protected Service logs when SMTP is absent:

```sh
kubectl --context kind-a13n-local -n a13n-dev logs deployment/a13n-a13n --tail=100
```

Accept the link, set the administrator password, and configure a Model Provider and Model. Keep initialization links private. If rebuilding with the same `local` tags, Helm does not detect image-content changes: after loading the images, restart `deployment/a13n-a13n` and `deployment/a13n-a13n-console` with `kubectl rollout restart`, then wait for both rollouts. Using a new tag for each build and setting both image tags on upgrade avoids this manual restart.

Closing the terminal does not stop the Pods or the fixed port mapping. Docker Desktop must remain running. PVC persistence survives Pod replacement, not deletion of the kind cluster; back up needed data before removing a cluster.

## AWS and GCP

Copy the relevant values file outside the repository and replace image, bucket, endpoint, domain and certificate placeholders. Prepare:

- A cluster with sufficient node/Pod capacity, network access to its dependencies and image-pull permissions. EKS managed EC2 nodes/Auto Mode and GKE Standard/Autopilot are possible hosts for ordinary Service Pods; validate cluster-specific admission and resource policies.
- PostgreSQL and a reachable Redis endpoint. This Chart does not create RDS, Cloud SQL, ElastiCache or Memorystore. The GCP example assumes a reachable private database endpoint; Cloud SQL Auth Proxy is not included.
- A compatible object bucket with permissions for bucket checking, listing, reading, conditional writes and deletes. AWS should use EKS Pod Identity or IRSA for the generated ServiceAccount (`a13n-a13n` for release `a13n`); set `serviceAccount.annotations` for IRSA and configure the trust relationship. There is no broad Kubernetes RBAC grant; default service-account token automount is disabled. Cloud identity integrations must supply their own supported projected credentials.
- An existing RWX filesystem claim named `a13n-files`. Adapt `examples/files-pvc.yaml` to an installed EFS/Filestore CSI StorageClass and apply it in the release namespace. The requested size is an example, not a universal provider minimum. Ensure UID/GID 10001 can write; EFS access points or NFS export permissions may need explicit setup. `fsGroup` alone does not configure remote export ownership.
- An ingress controller, DNS and TLS configuration matching `publicOrigin`. Ingress is disabled until these are prepared. The AWS overlay targets AWS Load Balancer Controller with an ACM certificate; Auto Mode ingress integration may require additional cluster configuration. The GCP overlay targets GKE Ingress with a TLS Secret. Apply `examples/gcp-backendconfig.yaml` and `examples/gcp-console-backendconfig.yaml` in the release namespace before enabling GKE Ingress; the GCP values supply the matching BackendConfig and NEG Service annotations. This requires a VPC-native cluster with GKE Ingress and its BackendConfig CRD. The example selects `/readyz` health checks and a one-hour backend timeout; validate connection behavior with actual SSE/WebSocket workloads. This Chart does not provision those cloud resources.

GCS is not a supported drop-in substitution for the S3 endpoint in this example. Service checks conditional object operations at startup. Supply a verified S3-compatible service, or implement and validate a native GCS adapter separately. Filestore is filesystem storage and does not replace the object backend. See the [Service configuration guide](../../docs/a13n-service/configuration.md) and [object-storage contract](../../spec/a13n-service/03-storage.md).

```sh
helm upgrade --install a13n deploy/kubernetes/a13n-service \
  --kube-context YOUR_CLOUD_CONTEXT --namespace YOUR_NAMESPACE \
  -f /absolute/private/path/values-cloud.yaml --wait --timeout 40m
```

The cloud values include a separate Console image repository/tag and two Console replicas. Build and push both images, then fill both image configurations. The existing cloud Ingress now serves Console at `/`, while Console routes `/api`, `/a2a`, `/ag-ui`, `/.well-known` and `/connection-authorizations` to Control, and `/connectivity` to Connectivity. Configure `ingress.host`, certificates and `publicOrigin` consistently. The GCP BackendConfig examples include separate health checks for backend port 8000 and Console port 8080. The Console Deployment does not mount application credentials or Service storage.

For separately hosted Console assets, set `console.enabled=false`; ingress then routes the configured paths directly to Control. Set appropriate paths and preserve provider ingress routes in the external front door.

## Agent execution environments

Kubernetes runs the Service Pod; the Environment Provider runs Agent commands. This Chart creates no per-Agent Pods, Docker daemon, privileged container or host Docker socket mount. Use the existing remote E2B or HTTP Envd Provider with protected credentials and network access. Distributed mode does not permit host-local Providers. Running Envd targets or implementing Kubernetes sandbox lifecycle management is a separate deployment concern.

## Validation and operations

These commands render locally and do not connect to a cluster:

```sh
helm lint deploy/kubernetes/a13n-service -f deploy/kubernetes/values-local.yaml --strict
helm lint deploy/kubernetes/a13n-service -f deploy/kubernetes/values-aws.yaml --strict
helm lint deploy/kubernetes/a13n-service -f deploy/kubernetes/values-gcp.yaml --strict
helm template a13n deploy/kubernetes/a13n-service -f deploy/kubernetes/values-local.yaml
```

Templates use `.tpl` because they contain Go template syntax rather than standalone YAML. Validate rendered manifests, not raw templates. Rendering does not verify image availability, Kubernetes admission, credentials, storage semantics, database connectivity or actual rollout behavior. The mounted TOML can be checked with `a13n-service --config PATH config check` using the same protected deployment environment; that command also does not establish connectivity.

Configuration changes roll Pods using a configuration checksum. The local data PVC has Helm's `keep` policy; PostgreSQL and Redis StatefulSet claims are retained by Kubernetes by default. Uninstalling does not delete these data volumes or external resources. Record retained claim names before uninstalling; use `persistence.existingClaim` to reuse the local Service data claim in a later installation. Do not delete PVCs to resolve startup failures. Back up PostgreSQL, object data, filesystem data and the credential encryption key independently.
