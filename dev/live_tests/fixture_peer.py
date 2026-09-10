"""An owned TLS peer with a temporary certificate, trusted only by lab processes."""

import ipaddress
import os
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

from .config import load_config
from .fixture_connectivity import connectivity_router


def create_certificate(root):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Live test loopback")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path, key_path, bundle_path = root / "peer.crt", root / "peer.key", root / "peer-ca-bundle.crt"
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    certificate_path.write_bytes(pem)
    bundle_path.write_bytes(Path(certifi.where()).read_bytes() + b"\n" + pem)
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    return {"peer_certificate": str(certificate_path), "peer_key": str(key_path), "peer_ca_bundle": str(bundle_path)}


def certificate_context(config):
    return ssl.create_default_context(cafile=config["peer_ca_bundle"])


def peer_app(config):
    app = FastAPI()
    app.include_router(
        connectivity_router(Path(config["workspace_root"]), {**config, "control_url": config["peer_url"]})
    )
    if "run_faults" in config:
        from .fixture_model import fixture_router
        from .host import bearer_authenticator

        # A bare upstream preserves abrupt streaming disconnects. Service's
        # HTTP middleware can turn a generator error into a graceful HTTP EOF.
        app.include_router(fixture_router(Path(config["workspace_root"]), bearer_authenticator(config)))

    @app.get("/readyz")
    async def ready():
        return {"status": "ready", "role": "test-peer"}

    return app


def main():
    config = load_config()
    uvicorn.run(
        peer_app(config),
        host="127.0.0.1",
        port=urlsplit(config["peer_url"]).port,
        ssl_keyfile=config["peer_key"],
        ssl_certfile=config["peer_certificate"],
        log_level="warning",
    )


if __name__ == "__main__":
    main()
