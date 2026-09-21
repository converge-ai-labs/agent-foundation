"""Canonical public error response bodies shared by HTTP boundaries."""

from __future__ import annotations


def api_error_body(
    request_id: str,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


__all__ = ["api_error_body"]
