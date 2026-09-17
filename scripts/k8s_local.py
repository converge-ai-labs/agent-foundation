"""Start the fixed, machine-local kind deployment without changing existing credentials."""

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
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "kind-a13n-local"
NAMESPACE = "a13n-dev"
ORIGIN = "http://127.0.0.1:8080"
SECRET_FILES = {"a13n-service-secrets": "service.env", "a13n-postgres-secrets": "postgres.env"}
MASTER_KEY = "A13N_SERVICE_SECRET_MASTER_KEY_BASE64"


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


def credentials(
    local: dict[str, dict[str, str]], remote: dict[str, dict[str, str]], *, retained: bool, email: str
) -> dict[str, dict[str, str]]:
    selected = dict(remote)
    for name, values in local.items():
        if name in selected and selected[name] != values:
            raise ValueError(f"Local and cluster credentials differ for {name}; reconcile them before starting")
        selected[name] = values
    if not selected:
        if retained:
            raise ValueError("Existing PVCs have no credentials; restore the original env files or Secrets")
        if "@" not in email or any(c.isspace() for c in email):
            raise ValueError("K8S_ADMIN_EMAIL must be an email address")
        password = secrets.token_hex(32)
        selected = {
            "a13n-service-secrets": {
                "A13N_SERVICE_DATABASE_URL": (
                    f"postgresql+psycopg://a13n_service:{password}@a13n-a13n-postgres:5432/a13n_service"
                ),
                "A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL": email,
                MASTER_KEY: base64.b64encode(secrets.token_bytes(32)).decode(),
                "A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID": "primary-v1",
            },
            "a13n-postgres-secrets": {
                "POSTGRES_USER": "a13n_service",
                "POSTGRES_DB": "a13n_service",
                "POSTGRES_PASSWORD": password,
            },
        }
    if set(selected) != set(SECRET_FILES):
        raise ValueError("Incomplete credentials; restore the missing service.env or postgres.env before starting")
    service, postgres = (selected[name] for name in SECRET_FILES)
    try:
        key = base64.b64decode(service[MASTER_KEY], validate=True)
        database = urlsplit(service["A13N_SERVICE_DATABASE_URL"])
        valid_database = (
            database.scheme == "postgresql+psycopg"
            and database.hostname == "a13n-a13n-postgres"
            and database.port == 5432
            and unquote(database.username or "") == postgres["POSTGRES_USER"]
            and unquote(database.password or "") == postgres["POSTGRES_PASSWORD"]
            and bool(postgres["POSTGRES_PASSWORD"])
            and database.path == "/" + postgres["POSTGRES_DB"]
        )
        valid_identity = bool(service["A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL"]) and bool(
            service["A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID"]
        )
    except (KeyError, ValueError, binascii.Error) as error:
        raise ValueError(
            "Credentials are missing required fields or have an invalid master key/database URL"
        ) from error
    if len(key) != 32 or not valid_database or not valid_identity:
        raise ValueError("Credentials require a 32-byte master key, admin email, and matching local database settings")
    return selected


def prepare_secrets(state: Path) -> None:
    local = {name: read_env(state / filename) for name, filename in SECRET_FILES.items() if (state / filename).exists()}
    remote = {}
    for name in SECRET_FILES:
        raw = kubectl("get", "secret", name, "--ignore-not-found", "-o", "json", capture=True)
        if raw.strip():
            remote[name] = {k: base64.b64decode(v).decode() for k, v in json.loads(raw)["data"].items()}
    retained = bool(json.loads(kubectl("get", "pvc", "-o", "json", capture=True))["items"])
    selected = credentials(
        local, remote, retained=retained, email=os.environ.get("K8S_ADMIN_EMAIL", "admin@example.com")
    )
    for name, values in selected.items():
        path = state / SECRET_FILES[name]
        if not path.exists():
            with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                stream.write("".join(f"{k}={v}\n" for k, v in values.items()))
        path.chmod(0o600)
    # Persist both files before the first cluster write so either Secret can be retried.
    for name, values in selected.items():
        if name not in remote:
            manifest = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name}, "stringData": values}
            kubectl("create", "-f", "-", capture=True, input_text=json.dumps(manifest))
    print(f"Credentials preserved in {state} (env files mode 600).", flush=True)


def bootstrap_link(logs: str) -> str | None:
    prefix = "Administrator initialization link (single use): "
    link = None
    for line in logs.splitlines():
        try:
            message = json.loads(line).get("message", "")
        except (ValueError, AttributeError):
            continue
        if message.startswith(prefix):
            candidate = message[len(prefix) :].strip()
            parsed = urlsplit(candidate)
            if candidate.startswith(ORIGIN + "/invitations/") and parsed.fragment.startswith("token="):
                link = candidate
    return link


def start(state: Path) -> None:
    for tool in ("docker", "kind", "kubectl", "helm"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} before running make k8s-up")
    run("docker", "info", capture=True)
    clusters = run("kind", "get", "clusters", capture=True).splitlines()
    if "a13n-local" not in clusters:
        print("Creating kind cluster a13n-local...", flush=True)
        run(
            "kind",
            "create",
            "cluster",
            "--name",
            "a13n-local",
            "--config",
            "deploy/kubernetes/kind-local.yaml",
            "--wait",
            "5m",
        )
    nodes = run("kind", "get", "nodes", "--name", "a13n-local", capture=True).splitlines()
    mappings = [
        json.loads(run("docker", "inspect", node, capture=True))[0]["HostConfig"]["PortBindings"] for node in nodes
    ]
    if not any({"HostIp": "127.0.0.1", "HostPort": "8080"} in m.get("30080/tcp", []) for m in mappings):
        raise ValueError("Existing kind cluster lacks 127.0.0.1:8080 -> 30080 mapping; no cluster was deleted")
    if not kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name", capture=True).strip():
        kubectl("create", "namespace", NAMESPACE)
    prepare_secrets(state)
    tag = "local-" + secrets.token_hex(6)
    images = [f"{name}:{tag}" for name in ("a13n-service", "a13n-console")]
    for name, image in zip(("a13n-service", "a13n-console"), images, strict=True):
        print(f"Building {image}...", flush=True)
        run("docker", "build", "--progress=plain", "-f", f"deploy/containers/{name}/Dockerfile", "-t", image, ".")
    run("kind", "load", "docker-image", *images, "--name", "a13n-local")
    print("Deploying; Helm waits for readiness (up to 40 minutes)...", flush=True)
    try:
        run(
            "helm",
            "upgrade",
            "--install",
            "a13n",
            "deploy/kubernetes/a13n-service",
            "--kube-context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
            "-f",
            "deploy/kubernetes/values-local.yaml",
            "--set-string",
            f"image.tag={tag}",
            "--set-string",
            f"console.image.tag={tag}",
            "--wait",
            "--timeout",
            "40m",
        )
    except RuntimeError:
        kubectl("get", "pods,pvc")
        print("Inspect Service logs and namespace events for startup failures; credentials and PVCs are retained.")
        raise
    kubectl("get", "pods,pvc")
    print(f"Console: {ORIGIN}/login", flush=True)
    logs = kubectl("logs", "deployment/a13n-a13n", capture=True)
    link = bootstrap_link(logs)
    if link:
        print(f"Administrator initialization (single use; keep private):\n{link}")
    else:
        print(
            "No new initialization link in this Pod. Sign in with your existing account.\n"
            "If initialization is still pending and its link was lost, run make k8s-admin-link."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "admin-link"))
    args = parser.parse_args()
    state = Path(os.environ.get("K8S_STATE_DIR", "~/.config/a13n-local")).expanduser().resolve()
    if state == ROOT or ROOT in state.parents:
        parser.error("K8S_STATE_DIR must be outside the checkout")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "operation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "up":
                start(state)
            else:
                print(
                    "Replacing a pending administrator invitation; completed initialization cannot be reopened.",
                    flush=True,
                )
                kubectl(
                    "exec",
                    "deployment/a13n-a13n",
                    "--",
                    "a13n-service",
                    "--config",
                    "/app/service.toml",
                    "iam",
                    "reissue-bootstrap",
                )
        except (OSError, ValueError, RuntimeError) as error:
            parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
