"""Shared finite, bounded JSON value validation for CodeAct boundaries."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, cast

from pydantic_ai.messages import BinaryContent


class JsonLimitExceeded(ValueError):
    pass


def bounded_json_size(value: Any, limit: int, *, allow_binary: bool = False) -> int:
    total = 0
    active: set[int] = set()
    pending: list[tuple[bool, Any]] = [(True, value)]
    while pending:
        entering, item = pending.pop()
        if not entering:
            active.remove(cast(int, item))
            continue
        if isinstance(item, str):
            increment = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        elif item is None or isinstance(item, bool | int):
            increment = len(json.dumps(item).encode())
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("CodeAct numbers must be finite")
            increment = len(json.dumps(item).encode())
        elif isinstance(item, BinaryContent) and allow_binary:
            increment = 4 * ((len(item.data) + 2) // 3) + 256
        elif isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise ValueError("Circular CodeAct value")
            if not all(isinstance(key, str) for key in item):
                raise TypeError("CodeAct maps require string keys")
            active.add(identity)
            increment = 2 + max(0, len(item) - 1)
            pending.append((False, identity))
            for key, nested in reversed(tuple(item.items())):
                total += len(json.dumps(key, ensure_ascii=False).encode()) + 1
                pending.append((True, nested))
        elif isinstance(item, list | tuple):
            identity = id(item)
            if identity in active:
                raise ValueError("Circular CodeAct value")
            active.add(identity)
            increment = 2 + max(0, len(item) - 1)
            pending.append((False, identity))
            pending.extend((True, nested) for nested in reversed(item))
        else:
            raise TypeError(f"Unsupported CodeAct value: {type(item).__name__}")
        total += increment
        if total > limit:
            raise JsonLimitExceeded
    return total
