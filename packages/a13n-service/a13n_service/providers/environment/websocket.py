"""Private per-instance bootstrap supplied by Service, never by a readable recipe."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class WebSocketBootstrap:
    device_id: str
    url: str
    credential: SecretStr = field(repr=False)
    wait_ready: Callable[[], Awaitable[None]] = field(repr=False)

    def environment(self, values: dict[str, str]) -> dict[str, str]:
        # Envd rejects mixed transport options. The launcher writes the secret to
        # a protected file and removes it from the daemon's environment.
        return {
            **{key: value for key, value in values.items() if not key.startswith("A13N_ENVD_HTTP_")},
            "A13N_ENVD_DEVICE_ID": self.device_id,
            "A13N_ENVD_TRANSPORT": "reverse_websocket",
            "A13N_ENVD_REVERSE_WS_URL": self.url,
            "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": "/var/lib/a13n-envd/service-token",
            "A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN": self.credential.get_secret_value(),
        }
