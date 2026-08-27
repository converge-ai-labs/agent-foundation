"""Validation boundary for package-owned agent-envd release metadata."""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import ValidationError

from a13n_ui.errors import ConfigurationError

from .models import EnvdRuntimeManifest

_RUNTIME_MANIFEST_NAME = "agent_envd_runtime_manifest.json"


def load_envd_runtime_manifest(content: bytes | None = None) -> EnvdRuntimeManifest | None:
    """Validate supplied release metadata or the package resource when one is shipped.

    Block 2 owns this fail-closed schema boundary. The release pipeline adds the
    concrete six-target manifest once a reviewed agent-envd release exists.
    """

    if content is None:
        resource = files("a13n_ui").joinpath(_RUNTIME_MANIFEST_NAME)
        if not resource.is_file():
            return None
        content = resource.read_bytes()
    try:
        raw = json.loads(content, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        return EnvdRuntimeManifest.model_validate(raw, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ConfigurationError(
            "The package-owned agent-envd runtime manifest is malformed or incomplete.",
            code="envd_runtime_manifest_invalid",
        ) from exc


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate runtime manifest key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite runtime manifest constant: {value}")


__all__ = ["load_envd_runtime_manifest"]
