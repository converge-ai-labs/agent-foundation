"""Provider-neutral shared desktop observations and bounded input gestures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import EnvironmentAction, EnvironmentOperationReceipt


class ComputerTarget(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_id: str
    name: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ComputerDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    targets: tuple[ComputerTarget, ...]
    observe_ready: bool
    input_ready: bool
    scroll_units: tuple[Literal["pixels", "steps"], ...] = ("pixels",)


class ComputerObservation(BaseModel):
    """Geometry reference pinned to one mount incarnation, not a freshness claim."""

    model_config = ConfigDict(frozen=True)
    mount_id: str
    observed_generation: str
    observation_id: str
    target_id: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    mime_type: Literal["image/jpeg", "image/png"]
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class ComputerScreenshot:
    observation: ComputerObservation
    data: bytes


class ComputerPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    x: int = Field(ge=0)
    y: int = Field(ge=0)


type ComputerButton = Literal["left", "right", "middle"]


class ComputerClick(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["click"] = "click"
    observation: ComputerObservation
    point: ComputerPoint
    button: ComputerButton = "left"
    count: int = Field(default=1, ge=1, le=2)


class ComputerMove(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["move"] = "move"
    observation: ComputerObservation
    point: ComputerPoint


class ComputerDrag(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["drag"] = "drag"
    observation: ComputerObservation
    start: ComputerPoint
    end: ComputerPoint
    button: ComputerButton = "left"
    duration_ms: int = Field(default=500, ge=100, le=3000)


class ComputerScroll(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["scroll"] = "scroll"
    observation: ComputerObservation
    point: ComputerPoint
    delta_x: int = Field(default=0, ge=-10000, le=10000)
    delta_y: int = Field(default=0, ge=-10000, le=10000)
    unit: Literal["pixels", "steps"] = "pixels"


class ComputerTypeText(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["type_text"] = "type_text"
    text: str = Field(min_length=1, max_length=16384)


class ComputerPressKeys(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["press_keys"] = "press_keys"
    keys: tuple[str, ...] = Field(min_length=1, max_length=8)


type ComputerInput = ComputerClick | ComputerMove | ComputerDrag | ComputerScroll | ComputerTypeText | ComputerPressKeys

COMPUTER_INPUT_ACTIONS = {
    "click": EnvironmentAction.COMPUTER_CLICK,
    "move": EnvironmentAction.COMPUTER_MOVE,
    "drag": EnvironmentAction.COMPUTER_DRAG,
    "scroll": EnvironmentAction.COMPUTER_SCROLL,
    "type_text": EnvironmentAction.COMPUTER_TYPE_TEXT,
    "press_keys": EnvironmentAction.COMPUTER_PRESS_KEYS,
}


@dataclass(frozen=True, slots=True)
class ComputerActionResult:
    receipt: EnvironmentOperationReceipt
    effect: Literal["not_executed", "executed", "partial", "unknown"]
    input_cleanup_complete: bool


class ProviderComputerOperations(Protocol):
    async def describe(self) -> ComputerDescription: ...

    async def observe(self, *, target_id: str | None = None, max_dimension: int = 1280) -> ComputerScreenshot: ...

    async def execute(self, request: ComputerInput) -> ComputerActionResult: ...
