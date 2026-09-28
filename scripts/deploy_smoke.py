"""Exercise a running deployment the way its first user does: Console, first administrator, sign-in, credentials."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import subprocess
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

from k8s_local import ADMIN_FILE, CONSOLE_URL, read_env, state_directory

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy/docker/compose/a13n-service.yaml"
COMPOSE_PROJECT = "a13n-compose-smoke"
KEY_FILE = "/app/var/encryption.key"


class Browser:
    """A cookie-keeping client that sends the Origin a browser on `base_url` sends."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.csrf = ""
        self.workspace = ""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, str, dict]:
        headers = {"Origin": self.base_url, "X-CSRF-Token": self.csrf}
        if self.workspace:
            headers["X-Workspace-ID"] = self.workspace
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.headers.get_content_type(), json_or_empty(response.read())
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get_content_type(), json_or_empty(error.read())

    def expect(self, status: int, method: str, path: str, body: dict | None = None) -> dict:
        actual, _, answer = self.request(method, path, body)
        if actual != status:
            # Error bodies carry no credentials; the code and message explain the failure.
            raise RuntimeError(f"{method} {path} answered {actual}, expected {status}: {answer.get('error', answer)}")
        return answer


def json_or_empty(content: bytes) -> dict:
    try:
        value = json.loads(content)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def check(base_url: str, email: str, password: str, *, first_run: bool) -> None:
    """Console loads; the administrator is created on first run, otherwise signs in; a credential can be stored."""
    browser = Browser(base_url)
    browser.expect(200, "GET", "/readyz")
    status, content_type, _ = browser.request("GET", "/login")
    if (status, content_type) != (200, "text/html"):
        raise RuntimeError(f"Console answered {status} {content_type}")
    initialized = browser.expect(200, "GET", "/api/v1/auth/configuration")["initialized"]
    if initialized == first_run:
        raise RuntimeError(f"Expected initialized={not first_run}, found {initialized}")
    credentials = {"email": email, "password": password}
    signed_in = browser.expect(
        200, "POST", "/api/v1/auth/bootstrap" if first_run else "/api/v1/auth/login", credentials
    )
    browser.csrf = signed_in["csrf_token"]
    browser.expect(200, "GET", "/api/v1/auth/session")
    browser.workspace = browser.expect(200, "GET", "/api/v1/workspaces")["items"][0]["id"]
    provider = {"type": "openai", "name": f"Smoke {secrets.token_hex(4)}", "credential": {"api_key": "sk-smoke"}}
    if not browser.expect(201, "POST", "/api/v1/model-providers", provider)["credential_configured"]:
        raise RuntimeError("The model provider did not keep its credential")
    if first_run:
        browser.expect(409, "POST", "/api/v1/auth/bootstrap", credentials)
    print(f"{base_url}: Console, {'bootstrap' if first_run else 'sign-in'} and credential storage work", flush=True)


def compose(*args: str, capture: bool = False, file: Path = COMPOSE_FILE, project: str = COMPOSE_PROJECT) -> str:
    command = ("docker", "compose", "-p", project, "-f", str(file), *args)
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=capture, check=False)
    if result.returncode:
        raise RuntimeError(f"docker compose {args[0]} failed (exit {result.returncode})")
    return result.stdout


def compose_smoke(port: str) -> None:
    """A fresh single-host stack runs its first user's path, then keeps its key and administrator across a restart."""
    os.environ["A13N_PORT"] = port
    base_url, password = f"http://127.0.0.1:{port}", secrets.token_urlsafe(24)
    try:
        compose("up", "--detach", "--wait")
        # Either loopback name reaches the Service; browsers commonly use `localhost`.
        check(f"http://localhost:{port}", "admin@example.com", password, first_run=True)
        key = compose("exec", "-T", "service", "sha256sum", KEY_FILE, capture=True)
        compose("restart", "service")
        compose("up", "--detach", "--wait")
        check(base_url, "admin@example.com", password, first_run=False)
        if compose("exec", "-T", "service", "sha256sum", KEY_FILE, capture=True) != key:
            raise RuntimeError("The generated encryption key changed across a restart")
    except RuntimeError:
        compose("logs", "--no-color", "--tail", "200", "service")
        raise
    finally:
        compose("down", "--volumes")


def quickstart_smoke(port: str) -> None:
    """The trial seeds once, retains credentials and resources, and never restores a changed password."""
    os.environ["A13N_PORT"] = port
    os.environ.setdefault("A13N_SERVICE_IMAGE", "a13n-service:local")
    stack = partial(
        compose,
        file=COMPOSE_FILE.with_name("a13n-service-quickstart.yaml"),
        project="a13n-quickstart-smoke",
    )
    base_url = f"http://127.0.0.1:{port}"
    email, password = "admin@example.com", "local-public-password-123"
    changed_password = secrets.token_urlsafe(24)
    try:
        stack("up", "--detach", "--wait")
        check(base_url, email, password, first_run=False)
        browser = Browser(base_url)
        browser.csrf = browser.expect(200, "POST", "/api/v1/auth/login", {"email": email, "password": password})[
            "csrf_token"
        ]
        workspaces = browser.expect(200, "GET", "/api/v1/workspaces")
        browser.workspace = workspaces["items"][0]["id"]
        providers = browser.expect(200, "GET", "/api/v1/model-providers")
        key = stack("exec", "-T", "service", "sha256sum", KEY_FILE, capture=True)
        browser.expect(
            204, "POST", "/api/v1/users/me/password", {"current_password": password, "password": changed_password}
        )
        stack("down")
        stack("up", "--detach", "--wait")
        browser = Browser(base_url)
        browser.expect(401, "POST", "/api/v1/auth/login", {"email": email, "password": password})
        browser.csrf = browser.expect(
            200, "POST", "/api/v1/auth/login", {"email": email, "password": changed_password}
        )["csrf_token"]
        assert browser.expect(200, "GET", "/api/v1/workspaces") == workspaces
        browser.workspace = workspaces["items"][0]["id"]
        assert browser.expect(200, "GET", "/api/v1/model-providers") == providers
        assert stack("exec", "-T", "service", "sha256sum", KEY_FILE, capture=True) == key
        print(
            "Quickstart: changed password, workspace, provider and encryption key survive re-initialization", flush=True
        )
    except (RuntimeError, AssertionError):
        stack("logs", "--no-color", "--tail", "200", "init", "service")
        raise
    finally:
        stack("down", "--volumes")


def kind_smoke() -> None:
    """The running local kind deployment serves Console and signs its generated administrator in."""
    admin = read_env(state_directory() / ADMIN_FILE)
    check(CONSOLE_URL, admin["EMAIL"], admin["PASSWORD"], first_run=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="target", required=True)
    stack = commands.add_parser("compose", help="Start, exercise and remove a disposable single-host stack")
    stack.add_argument("--port", default=os.environ.get("COMPOSE_SMOKE_PORT", "18080"))
    trial = commands.add_parser("quickstart", help="Exercise and remove a disposable pre-initialized trial stack")
    trial.add_argument("--port", default=os.environ.get("COMPOSE_SMOKE_PORT", "18080"))
    commands.add_parser("kind", help="Exercise the running local kind deployment")
    arguments = parser.parse_args()
    try:
        if arguments.target == "compose":
            compose_smoke(arguments.port)
        elif arguments.target == "quickstart":
            quickstart_smoke(arguments.port)
        else:
            kind_smoke()
    except (OSError, RuntimeError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
