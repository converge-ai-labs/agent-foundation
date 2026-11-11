"""A real Envd and a private HTTPS upstream, contained in one disposable Linux container."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import docker
import httpx2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from docker.errors import DockerException

from . import egress_guest
from .api import eventually
from .stack import docker_host, unavailable


def certificates(directory: Path) -> None:
    now = datetime.now(UTC)
    issuer_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Egress journey CA")])
    base = (
        x509.CertificateBuilder()
        .issuer_name(issuer)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
    )
    authority = (
        base.subject_name(issuer)
        .public_key(issuer_key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        base.subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, egress_guest.ALLOWED)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(egress_guest.ALLOWED), x509.DNSName(egress_guest.OTHER)]),
            critical=False,
        )
        .sign(issuer_key, hashes.SHA256())
    )
    for name, certificate in (("upstream", authority), ("server", server)):
        (directory / f"{name}.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = directory / "server.key"
    private.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    private.chmod(0o600)


@dataclass
class Guest:
    directory: Path
    endpoint: str

    @property
    def workspace(self) -> Path:
        return self.directory / "workspace"

    def requests(self) -> list[dict]:
        path = self.directory / "requests.jsonl"
        if not path.exists():
            return []
        # A concurrent final write may not yet have reached its terminating newline.
        return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


@asynccontextmanager
async def guest(
    directory: Path,
    config: pytest.Config,
    *,
    device_id: str,
    token: str,
    reverse_url: str | None = None,
    control_port: int | None = None,
    control_certificate: Path | None = None,
) -> AsyncIterator[Guest]:
    directory.mkdir()
    (directory / "workspace").mkdir()
    # Keep host-owned signals separate from the workspace reassigned to the guest UID.
    (directory / "workspace" / "control").mkdir()
    certificates(directory)
    credential = directory / "token"
    credential.write_text(token)
    credential.chmod(0o600)
    environment = {
        "A13N_ENVD_DEVICE_ID": device_id,
        "A13N_ENVD_DEFAULT_WORKING_DIRECTORY": "/workspace",
        "A13N_ENVD_EGRESS_MODE": "controlled",
        "A13N_ENVD_TRANSPORT": "http",
        "A13N_ENVD_HTTP_BIND": "0.0.0.0:8080",
        "A13N_ENVD_HTTP_CREDENTIAL_FILE": "/fixture/token",
        "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
    }
    if reverse_url is not None:
        assert control_certificate is not None and control_port is not None
        (directory / "control.pem").write_bytes(control_certificate.read_bytes())
        environment = {key: value for key, value in environment.items() if not key.startswith("A13N_ENVD_HTTP_")}
        environment.update(
            A13N_ENVD_TRANSPORT="reverse_websocket",
            A13N_ENVD_REVERSE_WS_URL=reverse_url,
            A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE="/fixture/token",
            A13N_ENVD_REVERSE_WS_CA_FILE="/fixture/control.pem",
        )
    (directory / "launch.json").write_text(json.dumps({"environment": environment, "control_port": control_port}))
    client = docker.DockerClient(base_url=docker_host())
    container = None
    image = os.environ.get("SANDBOX_IMAGE", "a13n-sandbox:local")
    try:
        try:
            await asyncio.to_thread(client.images.get, image)
        except DockerException as error:
            unavailable(config, f"Controlled egress needs a current standard SANDBOX_IMAGE: {error}")
        container = await asyncio.to_thread(
            client.containers.create,
            image,
            name=f"a13n-egress-e2e-{uuid4().hex[:12]}",
            entrypoint=["python3", "/egress_guest.py"],
            user="0",
            cap_add=["SYS_ADMIN", "NET_ADMIN"],
            security_opt=["seccomp=unconfined", "apparmor=unconfined"],
            ports={"8080/tcp": ("127.0.0.1", 0)},
            extra_hosts={"host.docker.internal": "host-gateway"},
            dns=["127.0.0.1"],
            volumes={
                str(directory): {"bind": "/fixture", "mode": "rw"},
                str(directory / "workspace"): {"bind": "/workspace", "mode": "rw"},
                str(Path(egress_guest.__file__).resolve()): {"bind": "/egress_guest.py", "mode": "ro"},
            },
        )
        await asyncio.to_thread(container.start)

        async def ready() -> bool:
            await asyncio.to_thread(container.reload)
            assert container.status == "running", (await asyncio.to_thread(container.logs)).decode()
            return (directory / "ready").exists()

        await eventually(ready)
        ports = container.ports
        assert ports is not None
        bindings = ports["8080/tcp"]
        assert bindings
        port = bindings[0]["HostPort"]
        if reverse_url is None:
            async with httpx2.AsyncClient(trust_env=False, timeout=1) as probe:

                async def listening() -> bool:
                    try:
                        await probe.get(f"http://127.0.0.1:{port}/")
                    except httpx2.TransportError:
                        return False
                    return True

                # Docker's published TCP port accepts connections before Envd is serving HTTP.
                await eventually(listening)
        yield Guest(directory, f"http://127.0.0.1:{port}")
    except BaseException as error:
        if container is not None:
            output = (await asyncio.to_thread(container.logs, tail=40)).decode(errors="replace")
            error.add_note("Envd guest log:\n" + output)
        raise
    finally:
        if container is not None:
            await asyncio.to_thread(container.remove, force=True)
        client.close()
