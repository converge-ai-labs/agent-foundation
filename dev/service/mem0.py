"""Operate checkout-owned Mem0 OSS infrastructure without changing product behavior."""

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from dev.mem0.configuration import FIXTURE_URL, configure

from .environment import ROOT, Environment

LOCAL_MEM0_CONFIG = ROOT / "dev/mem0/local.toml"


class Mem0Settings(BaseModel):
    """Checkout infrastructure only; never part of product Service settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    port: int = Field(default=18888, ge=1, le=65535)
    api_key: SecretStr = SecretStr("local-mem0-api-key")


def load_mem0_settings(path: Path) -> Mem0Settings:
    try:
        return Mem0Settings.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (ValidationError, tomllib.TOMLDecodeError):
        raise ValueError("Invalid local Mem0 configuration; check enabled, port, and api_key") from None


@dataclass(frozen=True)
class Mem0:
    environment: Environment
    settings: Mem0Settings = field(default_factory=Mem0Settings)

    @property
    def port(self) -> int:
        return self.settings.port

    @property
    def api_key(self) -> str:
        value = self.settings.api_key.get_secret_value()
        if not value.strip():
            raise ValueError("Local Mem0 api_key cannot be blank")
        return value

    def validate(self) -> None:
        settings = self.environment.settings
        _ = self.api_key
        occupied = {settings.service.port, urlsplit(settings.iam.public_origin).port, 18080}
        for address in (settings.database.url, settings.redis.url):
            if address is not None:
                occupied.add(urlsplit(address.get_secret_value()).port)
        query = settings.observability.query
        if query.provider == "langfuse":
            port = urlsplit(query.langfuse_base_url or "").port or 3000
            occupied.update((port, port + 1))
        if self.port in occupied:
            raise ValueError(
                "Mem0 port overlaps another local development service; choose a distinct port in the local Mem0 configuration"
            )
        for kind in ("LLM", "EMBEDDING"):
            prefix = f"MEM0_OSS_{kind}_"
            base = os.environ.get(prefix + "BASE_URL", FIXTURE_URL)
            if base != FIXTURE_URL:
                required = ("MODEL", "API_KEY", "DIMENSIONS") if kind == "EMBEDDING" else ("MODEL", "API_KEY")
                if any(not os.environ.get(prefix + name, "").strip() for name in required):
                    raise ValueError(
                        f"A custom {kind.lower()} endpoint requires " + ", ".join(prefix + name for name in required)
                    )
        try:
            dimensions = int(os.environ.get("MEM0_OSS_EMBEDDING_DIMENSIONS", "128"))
            if not 1 <= dimensions <= 16000:
                raise ValueError
            if os.environ.get("MEM0_OSS_EMBEDDING_BASE_URL", FIXTURE_URL) == FIXTURE_URL and dimensions != 128:
                raise ValueError
        except ValueError:
            raise ValueError(
                "Set MEM0_OSS_EMBEDDING_DIMENSIONS to the model's actual size (the local fixture uses 128)"
            ) from None

    def compose(self, *args: str, capture: bool = False) -> str:
        stopping = bool(args) and args[0] in {"stop", "down"}
        if not stopping:
            self.validate()
        env = {key: value for key, value in os.environ.items() if not key.startswith("MEM0_LOCAL_")}
        env.update(MEM0_LOCAL_PORT=str(self.port), MEM0_LOCAL_API_KEY=self.api_key)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "--project-name",
                self.environment.project + "-mem0",
                "--file",
                str(ROOT / "dev/mem0/compose.yaml"),
                *args,
            ],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout or ""

    def start(self) -> None:
        if not self.settings.enabled:
            return
        self.validate()
        self.compose("up", "-d", "--build", "--wait", "--wait-timeout", "180")
        try:
            with httpx2.Client(
                base_url=f"http://127.0.0.1:{self.port}/",
                headers={"X-API-Key": self.api_key},
                trust_env=False,
                follow_redirects=False,
                timeout=30,
            ) as client:
                configure(client, os.environ)
        except httpx2.HTTPError:
            raise RuntimeError(
                "Mem0 configuration or authentication check failed. Check the local Mem0 api_key, model settings and the OSS container logs; no reset was requested."
            ) from None
        print(f"Mem0 OSS: http://127.0.0.1:{self.port}")
        if os.environ.get("MEM0_OSS_EMBEDDING_BASE_URL", FIXTURE_URL) == FIXTURE_URL:
            print(
                "Mem0 uses deterministic local embeddings for development, not semantic inference. See dev/mem0/README.md to configure an embedding model."
            )
        print(
            "Agent memory is opt-in: create a Memory Provider, then set config.memory.provider_id on the Agent revision. See dev/mem0/README.md for the API workflow."
        )
