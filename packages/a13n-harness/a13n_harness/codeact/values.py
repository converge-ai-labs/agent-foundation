"""Shared finite, bounded JSON value validation for CodeAct boundaries."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic_ai.messages import BinaryContent


class JsonLimitExceeded(ValueError):
    pass


def _string_size(value: str, limit: int) -> int:
    # Each character needs at least one byte, plus the surrounding quotes.
    total = 2
    if len(value) + total > limit:
        raise JsonLimitExceeded
    for offset in range(0, len(value), 1024):
        total += len(json.dumps(value[offset : offset + 1024], ensure_ascii=False).encode("utf-8")) - 2
        if total > limit:
            raise JsonLimitExceeded
    return total


def _map_items(value: Mapping[Any, Any]) -> Iterator[Any]:
    for key, nested in value.items():
        if not isinstance(key, str):
            raise TypeError("CodeAct maps require string keys")
        yield key
        yield nested


def bounded_json_size(value: Any, limit: int, *, allow_binary: bool = False) -> int:
    """Measure compact UTF-8 JSON with auxiliary space proportional to depth.

    Container lower bounds reject oversized values before visiting their children;
    string encoding uses fixed-size chunks rather than allocating a full copy.
    """
    total = 0
    active: set[int] = set()
    pending: list[tuple[int | None, Iterator[Any]]] = [(None, iter((value,)))]
    while pending:
        identity, children = pending[-1]
        try:
            item = next(children)
        except StopIteration:
            pending.pop()
            if identity is not None:
                active.remove(identity)
            continue
        if isinstance(item, str):
            increment = _string_size(item, limit - total)
        elif item is None or isinstance(item, bool | int):
            increment = len(json.dumps(item))
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("CodeAct numbers must be finite")
            increment = len(json.dumps(item))
        elif isinstance(item, BinaryContent) and allow_binary:
            increment = 4 * ((len(item.data) + 2) // 3) + 256
        elif isinstance(item, Mapping | list | tuple):
            identity = id(item)
            if identity in active:
                raise ValueError("Circular CodeAct value")
            mapping = isinstance(item, Mapping)
            increment = 2 + max(0, len(item) - 1) + (len(item) if mapping else 0)
            minimum_children = len(item) * (3 if mapping else 1)
            if total + increment + minimum_children > limit:
                raise JsonLimitExceeded
            active.add(identity)
            pending.append((identity, _map_items(item) if mapping else iter(item)))
        else:
            raise TypeError(f"Unsupported CodeAct value: {type(item).__name__}")
        total += increment
        if total > limit:
            raise JsonLimitExceeded
    return total
