"""Shared typed native tool boundary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mcp.types import Tool
from pydantic import BaseModel, JsonValue

from a13n_service.interactions.attempts import AttemptContext

from .domain import JsonObject

if TYPE_CHECKING:
    from .native_context import InboundRunContext, NativeToolContext

CONVERSATION_REPLY_DESCRIPTION = (
    "Send a user-visible reply to the current conversation. Plain final model output is not sent to the conversation. "
    "After an interim update, use this tool again to report the final outcome, including any failure or blocker. "
    "Only claim a file or other result was delivered after its sending tool confirms success. "
    "If no reply is needed, remain silent."
)


class NativeActionObserver(Protocol):
    async def __call__(
        self, invoke: Callable[[], Awaitable[BaseModel]], arguments: BaseModel | None = None
    ) -> BaseModel: ...


@dataclass(frozen=True, slots=True)
class NativeAction:
    definition: Tool
    call: Callable[[JsonObject], Awaitable[JsonValue]]
    call_observed: Callable[[JsonObject, NativeActionObserver], Awaitable[JsonValue]] | None = None


def action[Arguments: BaseModel](
    name: str,
    model: type[Arguments],
    call: Callable[[Arguments], Awaitable[BaseModel]],
    *,
    hide_receipt: bool = False,
    description: str | None = None,
) -> NativeAction:
    async def invoke(arguments: JsonObject) -> JsonValue:
        return await invoke_observed(arguments, None)

    async def invoke_observed(arguments: JsonObject, observer: NativeActionObserver | None) -> JsonValue:
        parsed = model.model_validate(arguments)
        result = await call(parsed) if observer is None else await observer(lambda: call(parsed), parsed)
        value = result.model_dump(mode="json")
        # Current-context replies preserve the admitted binding. Receipts are typed provider evidence,
        # never model-authored authority and never instructions to replace the target.
        if hide_receipt and value.get("kind") == "succeeded":
            return {"kind": "succeeded"}
        return value

    return NativeAction(
        Tool(name=name, description=description or name.replace(".", " "), input_schema=model.model_json_schema()),
        invoke,
        invoke_observed,
    )


def credential(credentials: JsonObject, name: str) -> str:
    value = credentials.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError("nativecredentials_unavailable")
    return value


class NativeObservationFactory(Protocol):
    async def has_reply(self, *, attempt: "AttemptContext", context: "InboundRunContext") -> bool: ...

    def __call__(
        self,
        *,
        action: str,
        attempt: "AttemptContext",
        context: "NativeToolContext",
        workspace_id: str,
        account_version: int,
        credential_generation: int,
    ) -> NativeActionObserver | None: ...
