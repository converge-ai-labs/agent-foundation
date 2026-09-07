"""Shared typed native tool boundary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mcp.types import Tool
from pydantic import BaseModel, JsonValue

from .domain import JsonObject


@dataclass(frozen=True, slots=True)
class NativeAction:
    definition: Tool
    call: Callable[[JsonObject], Awaitable[JsonValue]]


def action[Arguments: BaseModel](
    name: str, model: type[Arguments], call: Callable[[Arguments], Awaitable[BaseModel]], *, hide_receipt: bool = False
) -> NativeAction:
    async def invoke(arguments: JsonObject) -> JsonValue:
        result = await call(model.model_validate(arguments))
        value = result.model_dump(mode="json")
        # Current-context replies preserve the admitted binding. Receipts are typed provider evidence,
        # never model-authored authority and never instructions to replace the target.
        if hide_receipt and value.get("kind") == "succeeded":
            return {"kind": "succeeded"}
        return value

    return NativeAction(
        Tool(name=name, description=name.replace(".", " "), inputSchema=model.model_json_schema()), invoke
    )


def credential(credentials: JsonObject, name: str) -> str:
    value = credentials.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError("nativecredentials_unavailable")
    return value
