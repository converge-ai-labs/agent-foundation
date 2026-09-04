"""Shared interaction application errors."""

from __future__ import annotations


class RunAcceptanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


__all__ = ["RunAcceptanceError"]
