"""Provider coordinates for scheduled work in an authenticated group."""

from a13n_service.connectivity.native_context import InboundRunContext


def is_group(context: InboundRunContext) -> bool:
    if context.provider_key == "slack":
        return context.provider_context.get("conversation_kind") in {"channel", "group"}
    return context.provider_key == "lark" and context.provider_context.get("chat_type") == "group"


def conversation_id(context: InboundRunContext) -> str | None:
    key = "channel_id" if context.provider_key == "slack" else "chat_id"
    value = context.provider_context.get(key)
    return value if isinstance(value, str) and value else None


def source_message_id(context: InboundRunContext) -> str:
    key = "root_thread_ts" if context.provider_key == "slack" else "message_id"
    value = context.provider_context.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("routine_source_message_unavailable")
    return value
