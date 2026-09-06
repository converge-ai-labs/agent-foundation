"""Private effective settings transfer; never persist or log this payload."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr

from a13n_service.settings import Settings


def runner_settings_payload(settings: Settings) -> dict[str, object]:
    def encode(value: object) -> str:
        if isinstance(value, SecretStr):
            return value.get_secret_value()
        if isinstance(value, Path):
            return str(value.resolve())
        raise TypeError("Unsupported Runner setting type")

    payload = json.loads(json.dumps(settings.model_dump(mode="python"), default=encode))
    payload.update(role="worker", auto_migrate=False)
    return payload
