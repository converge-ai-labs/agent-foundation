"""Narrow projection of Harness model facts into native ModelProfile."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles import ModelProfile, merge_profile


class ContextWindowProfileModel(WrapperModel):
    """Expose one Harness-owned context window through the native model profile."""

    def __init__(self, wrapped: Model, *, context_window: int) -> None:
        super().__init__(wrapped)
        self._context_window = context_window

    @property
    def profile(self) -> ModelProfile:
        try:
            base = self.wrapped.profile
        except NotImplementedError:
            base = None
        return merge_profile(base, ModelProfile(context_window=self._context_window))

    @property
    def context_window(self) -> int:
        return self._context_window

    def __copy__(self) -> ContextWindowProfileModel:
        return ContextWindowProfileModel(self.wrapped, context_window=self._context_window)

    def __deepcopy__(self, memo: dict[int, Any]) -> ContextWindowProfileModel:
        return ContextWindowProfileModel(
            deepcopy(self.wrapped, memo),
            context_window=self._context_window,
        )


def project_context_window(model: Model, context_window: int | None) -> Model:
    """Overlay a known Harness context window without changing provider behavior."""
    if context_window is None:
        return model
    if isinstance(model, ContextWindowProfileModel):
        if model.context_window == context_window:
            return model
        model = model.wrapped
    return ContextWindowProfileModel(model, context_window=context_window)


__all__ = ["ContextWindowProfileModel", "project_context_window"]
