"""The one resolver of local development: everything derived from a checkout's identity and ports.

The Service reads an ordinary settings file; nothing in `packages/` knows about checkouts. Stdlib only.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dev.service.instance import Instance, ensure_instance, load_instance

ROOT = Path(__file__).resolve().parents[2]
# Public, local-only identities; the stores listen on loopback only.
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "local-public-password-123"
DATABASE = "a13n_service_dev"
DATABASE_PASSWORD = "local-only-password"

Section = dict[str, object]


@dataclass(frozen=True, slots=True)
class Checkout:
    root: Path
    instance: Instance

    @classmethod
    def resolve(cls, root: Path = ROOT) -> Checkout:
        """Reserve this checkout's instance on first use."""
        return cls(root.resolve(), ensure_instance(root))

    @classmethod
    def load(cls, root: Path = ROOT) -> Checkout | None:
        """Read-only: None for a checkout that never reserved an instance."""
        instance = load_instance(root)
        return cls(root.resolve(), instance) if instance else None

    @property
    def id(self) -> str:
        return self.instance.id

    @property
    def state(self) -> Path:
        return self.root / "var/dev"

    @property
    def settings_file(self) -> Path:
        return self.state / "service.toml"

    @property
    def seed_report(self) -> Path:
        return self.state / "seed-report.md"

    @property
    def objects(self) -> Path:
        return self.state / "objects"

    @property
    def mem0_records(self) -> Path:
        """The records of the scripted model's fake mem0 server, kept across application restarts."""
        return self.state / "mem0.json"

    @property
    def environments(self) -> Path:
        """The base directory of seeded `local` environment templates."""
        return self.state / "environments"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def console_url(self) -> str:
        # A host of its own per checkout: browsers scope cookies by host, not port, so two checkouts' Consoles
        # keep separate login sessions. Browsers resolve every *.localhost name to loopback.
        return f"http://{self.id}.localhost:{self.instance.ports.console}"

    @property
    def service_url(self) -> str:
        return f"http://127.0.0.1:{self.instance.ports.service}"

    @property
    def model_url(self) -> str:
        return f"http://127.0.0.1:{self.instance.ports.model}/v1"

    @property
    def database_url(self) -> str:
        port = self.instance.ports.postgres
        return f"postgresql+psycopg://{DATABASE}:{DATABASE_PASSWORD}@127.0.0.1:{port}/{DATABASE}"

    @property
    def trace_environment(self) -> str:
        return f"local-{self.id}"

    def settings(self, telemetry: Section) -> dict[str, Section]:
        """The Service settings of this checkout; `telemetry` selects the trace backend."""
        ports = self.instance.ports
        return {
            "server": {"host": "127.0.0.1", "port": ports.service, "public_url": self.console_url},
            "database": {"url": self.database_url},
            "redis": {"url": f"redis://127.0.0.1:{ports.redis}/0"},
            "objects": {"root": str(self.objects)},
            "encryption": {"active_key_id": "local"},
            "encryption.keys": {"local": self._encryption_key()},
            # The scripted model and other local fixtures listen on loopback over plain HTTP.
            "providers": {"private_cidrs": ["127.0.0.0/8"], "require_https": False},
            # Development only: `local` environments are directories on this host, with no isolation boundary.
            "provisioning.local": {"enabled": True, "root": str(self.environments)},
            "telemetry": {"log_format": "pretty", **telemetry},
        }

    def write_settings(self, telemetry: Section) -> Path:
        # JSON strings, numbers, booleans and arrays are valid TOML values.
        lines = []
        for section, values in self.settings(telemetry).items():
            lines += [f"[{section}]", *(f"{key} = {json.dumps(value)}" for key, value in values.items()), ""]
        write_private(self.settings_file, "\n".join(lines))
        return self.settings_file

    def _encryption_key(self) -> str:
        """A random key kept per checkout, so credentials stay readable across restarts and resets."""
        path = self.state / "encryption.key"
        if not path.exists():
            write_private(path, base64.b64encode(secrets.token_bytes(32)).decode())
        return path.read_text().strip()


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
