"""Typed browser interaction requests; one action per call."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Ref(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ref"]
    ref: str


class Locator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["locator"]
    role: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=300)
    frame_index: int | None = Field(default=None, ge=0, le=31)

    @model_validator(mode="after")
    def has_filter(self):
        if not (self.role or self.name or self.text):
            raise ValueError("A locator requires role, name or text.")
        return self


type Target = Annotated[Ref | Locator, Field(discriminator="kind")]


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Visual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["visual"]
    screenshot: str
    x: float = Field(ge=0, lt=1, description="Horizontal fraction of the screenshot width, from the left.")
    y: float = Field(ge=0, lt=1, description="Vertical fraction of the screenshot height, from the top.")


type PointerTarget = Annotated[Ref | Locator | Visual, Field(discriminator="kind")]


class Click(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["click"]
    target: PointerTarget
    button: Literal["left", "right", "middle"] = "left"
    count: int = Field(default=1, ge=1, le=2)


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fill"]
    target: Target
    text: str = Field(max_length=16384)


class TypeText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["type"]
    target: Target
    text: str = Field(max_length=4096)


class Press(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["press"]
    key: str = Field(min_length=1, max_length=80)
    target: Target | None = None


class Hover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["hover"]
    target: PointerTarget


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["check"]
    target: Target
    checked: bool = True


class Select(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["select"]
    target: Target
    values: list[str] = Field(min_length=1, max_length=100)
    by: Literal["value", "label"] = "value"


class Scroll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["scroll"]
    y: int = Field(default=600, ge=-10000, le=10000)
    x: int = Field(default=0, ge=-10000, le=10000)
    target: Target | None = None


type Action = Annotated[Click | Fill | TypeText | Press | Hover | Check | Select | Scroll, Field(discriminator="kind")]


class ElementCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["element"]
    target: Target
    state: Literal["visible", "hidden", "enabled", "disabled", "checked", "unchecked"] = "visible"


class TextCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=1024)
    present: bool = True


class UrlCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["url"]
    text: str = Field(min_length=1, max_length=8192)


class LoadCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["load"]
    state: Literal["interactive", "complete"] = "complete"


type Condition = Annotated[ElementCondition | TextCondition | UrlCondition | LoadCondition, Field(discriminator="kind")]
