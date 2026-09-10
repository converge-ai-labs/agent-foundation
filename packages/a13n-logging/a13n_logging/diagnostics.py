"""Exception structure for diagnostics that must not copy execution payloads."""

from __future__ import annotations

from typing import Any


def exception_details(error: BaseException) -> list[dict[str, Any]]:
    """Retain types and stack locations, including suppressed causes and groups.

    Exception messages, notes, source lines, locals, requests, and response bodies
    can contain secrets or tool content. Do not serialize them into routine logs.
    """
    details: list[dict[str, Any]] = []
    pending: list[tuple[BaseException, int | None]] = [(error, None)]
    seen: set[int] = set()
    while pending and len(details) < 32:
        current, parent = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        frames = []
        trace = current.__traceback__
        while trace is not None:
            frames.append(
                {
                    "file": trace.tb_frame.f_code.co_filename,
                    "line": trace.tb_lineno,
                    "function": trace.tb_frame.f_code.co_name,
                }
            )
            trace = trace.tb_next
        item: dict[str, Any] = {
            "type": f"{type(current).__module__}.{type(current).__qualname__}",
            "parent": parent,
            "frames": frames[-64:],
        }
        # HTTP libraries and OSError expose useful numeric classifications without
        # requiring their text, headers, URLs, or optional package dependencies.
        try:
            for name in ("status_code", "errno"):
                value = getattr(current, name, None)
                if type(value) is int:
                    item[name] = value
            response = getattr(current, "response", None)
            status = getattr(response, "status_code", None)
            if type(status) is int:
                item["status_code"] = status
        except Exception:
            # Optional provider properties must not replace the original failure.
            pass
        index = len(details)
        details.append(item)
        if isinstance(current, BaseExceptionGroup):
            pending.extend((child, index) for child in reversed(current.exceptions))
        cause = current.__cause__ or current.__context__
        if cause is not None:
            pending.append((cause, index))
    return details
