"""Operate checkout-owned Mem0 OSS infrastructure without changing product behavior."""

import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2

from dev.mem0.configuration import FIXTURE_URL, configure

from .environment import ROOT, Environment


@dataclass(frozen=True)
class Mem0:
    environment: Environment

    @property
    def enabled(self) -> bool:
        settings = self.environment.settings.memory
        return settings.provider == "oss" and urlsplit(settings.base_url or "").hostname == "127.0.0.1"

    @property
    def port(self) -> int:
        return urlsplit(self.environment.settings.memory.base_url or "").port or 18888

    def validate(self) -> None:
        if not self.enabled:
            return
        settings = self.environment.settings
        url = urlsplit(settings.memory.base_url or "")
        if url.scheme != "http" or not url.port or url.path not in {"", "/"}:
            raise ValueError("Managed local Mem0 requires memory.base_url = 'http://127.0.0.1:PORT' without a path")
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
                "Mem0 port overlaps another local development service; choose a distinct memory.base_url port"
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
            if not self.enabled:
                raise ValueError("Select a local OSS memory.base_url in SERVICE_CONFIG before managing Mem0 containers")
        env = {key: value for key, value in os.environ.items() if not key.startswith("MEM0_LOCAL_")}
        if self.enabled:
            assert self.environment.settings.memory.api_key is not None
            env.update(
                MEM0_LOCAL_PORT=str(self.port),
                MEM0_LOCAL_API_KEY=self.environment.settings.memory.api_key.get_secret_value(),
            )
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
        if not self.enabled:
            return
        self.validate()
        self.compose("up", "-d", "--build", "--wait", "--wait-timeout", "180")
        settings = self.environment.settings.memory
        assert settings.api_key is not None and settings.base_url is not None
        try:
            with httpx2.Client(
                base_url=settings.base_url.rstrip("/") + "/",
                headers={"X-API-Key": settings.api_key.get_secret_value()},
                trust_env=False,
                follow_redirects=False,
                timeout=30,
            ) as client:
                configure(client, os.environ)
        except httpx2.HTTPError:
            raise RuntimeError(
                "Mem0 configuration or authentication check failed. Check memory.api_key, model settings and the OSS container logs; no reset was requested."
            ) from None
        print(f"Mem0 OSS: http://127.0.0.1:{self.port}")
        if os.environ.get("MEM0_OSS_EMBEDDING_BASE_URL", FIXTURE_URL) == FIXTURE_URL:
            print(
                "Mem0 uses deterministic local embeddings for development, not semantic inference. See dev/mem0/README.md to configure an embedding model."
            )
        print(
            "Agent memory is opt-in: set config.memory on the Agent revision. See dev/mem0/README.md for the API workflow."
        )
