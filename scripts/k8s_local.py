"""Start a machine-local kind deployment without changing existing credentials."""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import json
import os
import secrets
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = os.environ.get("K8S_CLUSTER", "a13n-local")
CONTEXT = f"kind-{CLUSTER}"
NAMESPACE = "a13n-service"
PORT = int(os.environ.get("K8S_PORT", "8080"))
CONSOLE_URL = f"http://127.0.0.1:{PORT}"
CONTROL = "deployment/a13n-a13n-control"
DATABASE_HOST = "a13n-a13n-postgres"
SERVICE_SECRET, POSTGRES_SECRET = "a13n-service-secrets", "a13n-postgres-secrets"
SECRET_FILES = {SERVICE_SECRET: "service.env", POSTGRES_SECRET: "postgres.env"}
ADMIN_FILE = "admin.env"
ALREADY_BOOTSTRAPPED = 3  # `a13n-service bootstrap` exit status when an organization already exists


def state_directory() -> Path:
    """Where the launcher keeps this machine's credentials and administrator record."""
    default = "~/.config/a13n-service-kind" + (f"-{CLUSTER}" if CLUSTER != "a13n-local" else "")
    return Path(os.environ.get("K8S_STATE_DIR", default)).expanduser().resolve()


def run(*args: str, capture: bool = False, input_text: str | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, input=input_text, capture_output=capture, check=False)
    if result.returncode:
        # Captured commands may contain credential values in their output.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode}); captured output withheld")
    return result.stdout if capture else ""


def kubectl(*args: str, capture: bool = False, input_text: str | None = None) -> str:
    return run("kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args, capture=capture, input_text=input_text)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError(f"Invalid or duplicate entry in {path}")
        values[key] = value
    return values


def write_private(path: Path, values: dict[str, str]) -> None:
    """Create a mode-600 env file once; an existing file is never replaced."""
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        stream.write("".join(f"{k}={v}\n" for k, v in values.items()))


def credentials(
    local: dict[str, dict[str, str]], remote: dict[str, dict[str, str]], *, retained: bool
) -> dict[str, dict[str, str]]:
    selected = dict(remote)
    for name, values in local.items():
        if name in selected and selected[name] != values:
            raise ValueError(f"Local and cluster credentials differ for {name}; reconcile them before starting")
        selected[name] = values
    if not selected:
        if retained:
            raise ValueError("Existing PVCs have no credentials; restore the original env files or Secrets")
        password = secrets.token_hex(32)
        selected = {
            SERVICE_SECRET: {
                "A13N_DATABASE__URL": (
                    f"postgresql+psycopg://a13n_service:{password}@{DATABASE_HOST}:5432/a13n_service"
                ),
                "A13N_ENCRYPTION__ACTIVE_KEY_ID": "primary",
                "A13N_ENCRYPTION__KEYS": json.dumps({"primary": base64.b64encode(secrets.token_bytes(32)).decode()}),
            },
            POSTGRES_SECRET: {
                "POSTGRES_USER": "a13n_service",
                "POSTGRES_DB": "a13n_service",
                "POSTGRES_PASSWORD": password,
            },
        }
    if set(selected) != set(SECRET_FILES):
        raise ValueError("Incomplete credentials; restore the missing service.env or postgres.env before starting")
    service, postgres = selected[SERVICE_SECRET], selected[POSTGRES_SECRET]
    try:
        keys = json.loads(service["A13N_ENCRYPTION__KEYS"])
        valid_keys = (
            isinstance(keys, dict)
            and service["A13N_ENCRYPTION__ACTIVE_KEY_ID"] in keys
            and all(len(base64.b64decode(key, validate=True)) == 32 for key in keys.values())
        )
        database = urlsplit(service["A13N_DATABASE__URL"])
        valid_database = (
            database.scheme == "postgresql+psycopg"
            and database.hostname == DATABASE_HOST
            and database.port == 5432
            and unquote(database.username or "") == postgres["POSTGRES_USER"]
            and unquote(database.password or "") == postgres["POSTGRES_PASSWORD"]
            and bool(postgres["POSTGRES_PASSWORD"])
            and database.path == "/" + postgres["POSTGRES_DB"]
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as error:
        raise ValueError("Credentials are missing required fields or have an invalid key ring/database URL") from error
    if not valid_keys or not valid_database:
        raise ValueError("Credentials require 32-byte encryption keys and matching local database settings")
    return selected


def administrator(state: Path, email: str, *, retained: bool) -> dict[str, str] | None:
    """The administrator to bootstrap, created once for fresh data; None when existing data has no local record."""
    path = state / ADMIN_FILE
    if path.exists():
        return read_env(path)
    if retained:
        return None
    if "@" not in email or any(c.isspace() for c in email):
        raise ValueError("K8S_ADMIN_EMAIL must be an email address")
    values = {"EMAIL": email, "PASSWORD": secrets.token_urlsafe(24)}
    write_private(path, values)
    return values


def prepare(state: Path) -> dict[str, str] | None:
    """Persist credentials and the administrator record before the first cluster write, so reruns reuse them."""
    local = {name: read_env(state / filename) for name, filename in SECRET_FILES.items() if (state / filename).exists()}
    remote = {}
    for name in SECRET_FILES:
        raw = kubectl("get", "secret", name, "--ignore-not-found", "-o", "json", capture=True)
        if raw.strip():
            remote[name] = {k: base64.b64decode(v).decode() for k, v in json.loads(raw)["data"].items()}
    retained = bool(json.loads(kubectl("get", "pvc", "-o", "json", capture=True))["items"])
    selected = credentials(local, remote, retained=retained)
    for name, values in selected.items():
        path = state / SECRET_FILES[name]
        if not path.exists():
            write_private(path, values)
        path.chmod(0o600)
    admin = administrator(state, os.environ.get("K8S_ADMIN_EMAIL", "admin@example.com"), retained=retained)
    for name, values in selected.items():
        if name not in remote:
            manifest = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name}, "stringData": values}
            kubectl("create", "-f", "-", capture=True, input_text=json.dumps(manifest))
    print(f"Credentials preserved in {state} (env files mode 600).", flush=True)
    return admin


def bootstrap(admin: dict[str, str]) -> bool:
    """Create the administrator; False when the Service was already initialized."""
    password = admin["PASSWORD"]
    result = subprocess.run(
        [
            *("kubectl", "--context", CONTEXT, "-n", NAMESPACE, "exec", "-i", CONTROL, "--"),
            *("a13n-service", "--config", "/app/service.toml", "bootstrap", "--email", admin["EMAIL"]),
            "--password-stdin",
        ],
        cwd=ROOT,
        text=True,
        # Standard input keeps the password out of process arguments.
        input=f"{password}\n",
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == ALREADY_BOOTSTRAPPED:
        return False
    raise RuntimeError(f"Administrator bootstrap failed (exit {result.returncode}); inspect {CONTROL} logs")


def prepare_image(component: str, destination: str) -> str:
    """Build locally by default; CI can supply its already-built checkout image."""
    variable = "K8S_SERVICE_IMAGE" if component == "a13n-service" else "K8S_SANDBOX_IMAGE"
    source = os.environ.get(variable)
    cached = os.environ.get("K8S_BUILD_CACHE") == "gha"
    if not source:
        print(f"Building {destination}...", flush=True)
        build = ("build",)
        if cached:
            scope = "ci-service" if component == "a13n-service" else "service-e2e-sandbox"
            build = (
                "buildx",
                "build",
                "--load",
                "--cache-from",
                f"type=gha,version=2,scope={scope}",
            )
        binary_args = (
            ("--build-arg", "ENVD_BINARY_SOURCE=prebuilt")
            if component == "sandbox" and os.environ.get("K8S_SANDBOX_BINARY_SOURCE") == "prebuilt"
            else ()
        )
        run(
            "docker",
            *build,
            *binary_args,
            "--progress=plain",
            "-f",
            f"deploy/docker/images/{component}/Dockerfile",
            "-t",
            destination,
            ".",
        )
    if source or cached:
        # Reuse the content tag so kind does not import unchanged image layers again.
        # Inspect requires a local image; it never pulls a mutable registry fallback.
        source = source or destination
        image_id = run("docker", "image", "inspect", source, "--format", "{{.Id}}", capture=True).strip()
        destination = f"{destination.split(':')[0]}:local-{image_id.removeprefix('sha256:')}"
        run("docker", "tag", source, destination)
    return destination


def prepare_and_load_image(component: str, destination: str) -> str:
    image = prepare_image(component, destination)
    run("kind", "load", "docker-image", image, "--name", CLUSTER)
    return image


def start(state: Path, *, environments: bool = False) -> None:
    for tool in ("docker", "kind", "kubectl", "helm"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} before running make k8s-up")
    run("docker", "info", capture=True)
    if CLUSTER not in run("kind", "get", "clusters", capture=True).splitlines():
        print(f"Creating kind cluster {CLUSTER}...", flush=True)
        run(
            "kind",
            "create",
            "cluster",
            "--name",
            CLUSTER,
            "--config",
            "-",
            "--wait",
            "5m",
            input_text=(ROOT / "deploy/kubernetes/kind-local.yaml")
            .read_text()
            .replace("hostPort: 8080", f"hostPort: {PORT}"),
        )
    nodes = run("kind", "get", "nodes", "--name", CLUSTER, capture=True).splitlines()
    mappings = [
        json.loads(run("docker", "inspect", node, capture=True))[0]["HostConfig"]["PortBindings"] for node in nodes
    ]
    if not any({"HostIp": "127.0.0.1", "HostPort": str(PORT)} in m.get("30080/tcp", []) for m in mappings):
        raise ValueError(f"Existing kind cluster lacks 127.0.0.1:{PORT} -> 30080 mapping; no cluster was deleted")
    if not kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name", capture=True).strip():
        kubectl("create", "namespace", NAMESPACE)
    admin = prepare(state)
    tag = "local-" + secrets.token_hex(6)
    # The sandbox is first consumed by smoke runs, not migration or Service startup.
    # Join both image operations before returning, including on deployment failure.
    with ThreadPoolExecutor(max_workers=2) as pool:
        service = pool.submit(prepare_and_load_image, "a13n-service", f"a13n-service:{tag}")
        sandbox = pool.submit(prepare_and_load_image, "sandbox", f"a13n-sandbox:{tag}") if environments else None
        image = service.result()
        deploy(state, image, tag, admin, environments=environments)
        sandbox_image = sandbox.result() if sandbox is not None else None
    (state / "deployment.json").write_text(json.dumps({"service_image": image, "sandbox_image": sandbox_image}))


def deploy(state: Path, image: str, tag: str, admin: dict[str, str] | None, *, environments: bool) -> None:
    overlay = ("-f", "deploy/kubernetes/helm/values-environments-smoke.yaml") if environments else ()
    print("Deploying; Helm waits for migration and readiness (up to 20 minutes)...", flush=True)
    try:
        run(
            "helm",
            "upgrade",
            "--install",
            "a13n",
            "deploy/kubernetes/helm/a13n-service",
            "--kube-context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
            "-f",
            "deploy/kubernetes/helm/values-local.yaml",
            *overlay,
            "--set-string",
            f"publicUrl={CONSOLE_URL}",
            "--set-string",
            f"image.tag={image.split(':')[1]}",
            # Even unchanged images must exercise rolling replacement on each invocation.
            "--set-string",
            f"podAnnotations.a13n-local-rollout={tag}",
            "--wait",
            "--timeout",
            "20m",
        )
    except RuntimeError:
        kubectl("get", "pods,jobs,pvc")
        print("Inspect migration Job and Service logs and namespace events; credentials and PVCs are retained.")
        raise
    kubectl("get", "pods,pvc")
    print(f"Console: {CONSOLE_URL}", flush=True)
    if admin is None:
        print(
            "Sign in with your existing administrator. An uninitialized installation can create one with:\n"
            f"kubectl --context {CONTEXT} -n {NAMESPACE} exec -it {CONTROL} -- "
            "a13n-service --config /app/service.toml bootstrap --email you@example.com"
        )
        return
    created = bootstrap(admin)
    print(
        f"{'Created administrator' if created else 'Sign in as'} {admin['EMAIL']}; "
        f"the initial password is in {state / ADMIN_FILE} (mode 600)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up",))
    parser.parse_args()
    state = state_directory()
    if state == ROOT or ROOT in state.parents:
        parser.error("K8S_STATE_DIR must be outside the checkout")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "operation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start(state, environments=os.environ.get("K8S_ENVIRONMENTS") == "1")
        except (OSError, ValueError, RuntimeError) as error:
            parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
