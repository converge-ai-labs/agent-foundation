"""Transport configuration is independent of application reception policy."""

import hashlib

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.management import canonical_json


def connection_key(provider: str, config: JsonObject) -> str:
    app = config.get("api_app_id") if provider == "slack" else config.get("app_id")
    return hashlib.sha256(
        canonical_json({"provider": provider, "app": app, "origin": config.get("open_api_origin")}).encode()
    ).hexdigest()


def validate_credentials(provider: str, config: JsonObject, credentials: JsonObject) -> None:
    if provider not in {"slack", "lark"}:
        return
    websocket = config.get("event_transport", "http") == "websocket"
    required = (
        ("app_token" if websocket else "signing_secret")
        if provider == "slack"
        else ("app_secret" if websocket else "verification_token")
    )
    if not isinstance(credentials.get(required), str) or not credentials[required]:
        raise NativeError(
            "transport_credentials_required",
            f"The selected event transport requires {required}.",
            category=ErrorCategory.invalid_input,
        )
    if (
        provider == "lark"
        and websocket
        and config.get("open_api_origin") not in {"https://open.feishu.cn", "https://open.larksuite.com"}
    ):
        raise NativeError(
            "unsupported_transport_origin",
            "Long connections require an official Feishu or Lark API origin.",
            category=ErrorCategory.invalid_input,
        )
