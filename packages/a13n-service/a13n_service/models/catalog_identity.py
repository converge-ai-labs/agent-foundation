"""Conservative display grouping against models.dev's independent model directory."""

import re
from collections.abc import Mapping
from typing import Any


def model_identities(models: Any) -> dict[str, tuple[str, str]]:
    if not isinstance(models, Mapping):
        return {}
    if len(models) > 50_000:
        raise ValueError("model catalog exceeds its identity limit")
    candidates: dict[str, list[tuple[str, str]]] = {}
    for identity, value in models.items():
        if not isinstance(identity, str) or not isinstance(value, Mapping):
            continue
        name = value.get("name")
        if not isinstance(name, str):
            continue
        lab, separator, model = identity.partition("/")
        if not separator:
            continue
        for alias in {identity, model, f"{lab}.{model}"}:
            candidates.setdefault(_key(alias), []).append((identity, name))
    return {key: values[0] for key, values in candidates.items() if len(set(values)) == 1}


def catalog_identity(
    provider: str, model: str, name: str, identities: Mapping[str, tuple[str, str]]
) -> tuple[str, str]:
    candidate = model
    if provider == "amazon-bedrock":
        candidate = re.sub(r"^(?:us|eu|au|jp|in|global)\.", "", candidate)
    elif provider == "google-vertex":
        candidate = candidate.removesuffix("@default")
    # Exact catalog IDs only, with punctuation normalized across channels. Keep
    # dates, editions and deployment suffixes; never group by display-name guesses.
    return identities.get(_key(candidate), (f"{provider}/{model}", name))


def _key(value: str) -> str:
    return re.sub(r"[./_-]", "-", value.lower())
