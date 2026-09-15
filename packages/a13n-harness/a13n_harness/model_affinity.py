"""Shared authoring contract for opt-in gateway session affinity.

Presets are suggestions for header names, not gateway detection or routing policy.
Hosts bind the value to the current Thread at Model resolution or request time.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class SessionAffinityPreset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    header: str
    description: str


SESSION_AFFINITY_PRESETS = (
    SessionAffinityPreset(
        label="LiteLLM",
        header="x-litellm-session-id",
        description="Requires session affinity to be enabled on the gateway.",
    ),
    SessionAffinityPreset(
        label="Conversation ID",
        header="x-conversation-id",
        description="For gateways configured to route by this header; configure the routing rule first.",
    ),
    SessionAffinityPreset(
        label="Bifrost (API-key affinity)",
        header="x-bf-session-id",
        description="API-key affinity only; does not guarantee weighted provider or target pinning.",
    ),
    SessionAffinityPreset(
        label="X-Session-ID (legacy / custom)",
        header="x-session-id",
        description="Use only when your gateway is configured to recognize this header.",
    ),
)

# These names already belong to HTTP, authentication, attribution, or native
# session protocols. A Thread ID must not replace their values.
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "authorization",
        "api-key",
        "x-api-key",
        "proxy-authorization",
        "x-goog-api-key",
        "x-goog-user-project",
        "anthropic-version",
        "anthropic-beta",
        "x-amz-date",
        "x-amz-security-token",
        "x-amz-content-sha256",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
        "http-referer",
        "x-title",
        "session-id",
        "thread-id",
        "x-client-request-id",
    }
)


def validate_session_affinity_header(value: str) -> str:
    """Normalize one HTTP field name; do not accept a header value or template."""
    if not isinstance(value, str) or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", value) is None:
        raise ValueError("session_affinity_header must be an HTTP header name (1-128 ASCII characters)")
    name = value.lower()
    if name in _RESERVED_HEADERS:
        raise ValueError("session_affinity_header conflicts with a transport, authentication, or protocol header")
    return name


SessionAffinityHeader = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$",
        json_schema_extra={
            "x-session-affinity-presets": [preset.model_dump() for preset in SESSION_AFFINITY_PRESETS],
        },
    ),
    AfterValidator(validate_session_affinity_header),
]
