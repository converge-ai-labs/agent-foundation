"""Shared value rules for resource classification labels."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlalchemy import JSON, ColumnElement, cast, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import InstrumentedAttribute

LABELS_SQL_TYPE = JSON().with_variant(JSONB(), "postgresql")

LABEL_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$"
LabelKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=63, pattern=LABEL_KEY_PATTERN),
]


def _validate_label_value(value: str) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("label values must not contain control or surrogate characters")
    return value


LabelValue = Annotated[
    str,
    StringConstraints(strict=True, max_length=256),
    AfterValidator(_validate_label_value),
]


Labels = Annotated[
    dict[LabelKey, LabelValue],
    Field(max_length=32),
    AfterValidator(lambda labels: dict(sorted(labels.items()))),
]
_LABELS = TypeAdapter(Labels)


def validate_labels(value: Mapping[str, str]) -> dict[str, str]:
    """Apply the public value contract to internal merges and persisted maps."""
    return _LABELS.validate_python(dict(value))


class LabelsBody(BaseModel):
    """Complete replacement body for a resource label map."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    labels: Labels


def merge_labels(source: Mapping[str, str], overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy source labels and apply explicit creation-time overrides."""

    merged = dict(source)
    merged.update(overrides or {})
    return validate_labels(merged)


def labels_etag(resource_id: str, labels: Mapping[str, str]) -> str:
    """Return a strong tag bound only to identity and canonical label content."""

    canonical = json.dumps(validate_labels(labels), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(f"{resource_id}\0{canonical}".encode()).hexdigest()
    return f'"{digest}"'


def parse_label_filters(values: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``label=key=value`` values into exact AND predicates."""

    if len(values) > 64:
        raise ValueError("at most 64 label filter parameters are allowed")
    filters: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or len(value) > 320:
            raise ValueError("label filter is too long")
        key, separator, item = value.partition("=")
        if not separator:
            raise ValueError("label filters must use key=value")
        if key in filters and filters[key] != item:
            raise ValueError("label filters cannot contain conflicting values for one key")
        filters[key] = item
    return validate_labels(filters)


def _canonical_filter_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{key}={value}" for key, value in parse_label_filters(values).items())


LabelFilterValues = Annotated[
    tuple[Annotated[str, StringConstraints(strict=True, max_length=320)], ...],
    Field(max_length=64),
    AfterValidator(_canonical_filter_values),
]


def label_predicates(
    column: ColumnElement[dict[str, str]] | InstrumentedAttribute[dict[str, str]],
    labels: Mapping[str, str],
    *,
    dialect: str,
) -> tuple[ColumnElement[bool], ...]:
    """Build exact containment predicates for the supported SQL dialects."""

    canonical = validate_labels(labels)
    if not canonical:
        return ()
    if dialect == "postgresql":
        return (column.op("@>")(cast(canonical, JSONB)),)
    return tuple(func.json_extract(column, f'$."{key}"') == value for key, value in canonical.items())
