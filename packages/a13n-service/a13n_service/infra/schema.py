"""Independent JSON schemas for the static model classes registered in Service catalogs."""

import json
from functools import lru_cache

from pydantic import BaseModel, JsonValue


@lru_cache(maxsize=256)
def _encoded(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema())


def model_schema(model: type[BaseModel]) -> dict[str, JsonValue]:
    """Compile a registered model once, returning a fresh nested document for every catalog entry."""
    return json.loads(_encoded(model))
