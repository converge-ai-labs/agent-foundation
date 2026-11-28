"""Composio v3.1 event directory, project webhook and V3 signed deliveries."""

import base64
import hmac
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode

from pydantic import SecretStr

from ..contracts import ConnectorProviderError, JsonObject
from ..directory import DirectoryBudget, directory_items
from ..events import Event, EventInstance, EventType, EventWebhook
from ..http import ConnectorHttpClient
from ..validation import optional_string, path_segment, required_object, required_string
from .configuration import COMPOSIO_ENDPOINT


def verify_event(body: bytes, headers: Mapping[str, str], secret: str, *, now: datetime) -> Event:
    """Authenticate raw bytes before parsing. Composio signs with the literal secret."""
    try:
        source_id = headers["webhook-id"]
        timestamp = headers["webhook-timestamp"]
        if not source_id or len(source_id) > 256 or abs(now.timestamp() - int(timestamp)) > 300:
            raise ValueError("invalid timestamp or identity")
        signed = source_id.encode() + b"." + timestamp.encode() + b"." + body
        expected = base64.b64encode(hmac.digest(secret.encode(), signed, "sha256")).decode()
        signatures = headers["webhook-signature"].split()
        if not any(value.startswith("v1,") and hmac.compare_digest(expected, value[3:]) for value in signatures):
            raise ValueError("signature mismatch")
        value = required_object(json.loads(body))
        if value.get("id") != source_id or value.get("type") != "composio.trigger.message":
            raise ValueError("invalid V3 event")
        meta = required_object(value["metadata"])
        return Event(
            source_id=source_id,
            instance_id=required_string(meta, "trigger_id"),
            account_id=required_string(meta, "connected_account_id"),
            user_id=required_string(meta, "user_id"),
            event_type=required_string(meta, "trigger_slug"),
            occurred_at=datetime.fromisoformat(required_string(value, "timestamp")),
            payload=required_object(value["data"]),
        )
    except (KeyError, ValueError, TypeError, ConnectorProviderError) as error:
        raise ConnectorProviderError("invalid_event_signature_or_payload") from error


def _definition(value: JsonObject, app: str) -> EventType:
    if required_object(value["toolkit"]).get("slug") != app:
        raise ConnectorProviderError("provider_mismatch")
    return EventType(
        key=required_string(value, "slug"),
        app=app,
        name=required_string(value, "name"),
        description=required_string(value, "description"),
        instructions=optional_string(value.get("instructions")) or "",
        version=required_string(value, "version"),
        config_schema=required_object(value["config"]),
        payload_schema=required_object(value["payload"]),
        requires_provider_setup=value.get("requires_webhook_endpoint_setup") is True,
    )


def _webhook(value: JsonObject) -> EventWebhook:
    events = value.get("enabled_events")
    if value.get("version") != "V3" or not isinstance(events, list) or "composio.trigger.message" not in events:
        raise ConnectorProviderError("incompatible_webhook")
    return EventWebhook(
        id=required_string(value, "id"),
        url=required_string(value, "webhook_url"),
        secret=SecretStr(required_string(value, "secret")),
    )


class ComposioEvents:
    def __init__(self, http: ConnectorHttpClient, api_key: str):
        self.http, self.api_key = http, api_key

    async def _request(
        self, method: Literal["GET", "POST", "PATCH", "DELETE"], path: str, *, body: JsonObject | None = None
    ) -> JsonObject:
        return required_object(
            await self.http.request(
                method,
                endpoint=COMPOSIO_ENDPOINT,
                path="/api/v3.1" + path,
                api_key=self.api_key,
                json_body=body,
                write=method != "GET",
            )
        )

    async def event_types(self, app: str) -> list[EventType]:
        items = await directory_items(
            self.http,
            endpoint=COMPOSIO_ENDPOINT,
            api_key=self.api_key,
            path="/api/v3.1/triggers_types?" + urlencode({"toolkit_slugs": app}),
            budget=DirectoryBudget(),
            page_size=50,
        )
        return [_definition(item, app) for item in items]

    async def event_type(self, app: str, key: str) -> EventType:
        result = _definition(await self._request("GET", f"/triggers_types/{path_segment(key)}"), app)
        if result.key != key:
            raise ConnectorProviderError("provider_mismatch")
        return result

    async def webhook(self) -> EventWebhook | None:
        value = await self._request("GET", "/webhook_subscriptions?limit=2")
        items = value.get("items")
        if not isinstance(items, list) or len(items) > 1 or value.get("next_cursor"):
            raise ConnectorProviderError("ambiguous_webhook")
        return _webhook(required_object(items[0])) if items else None

    async def configure_webhook(self, url: str, existing_id: str | None, *, rotate: bool) -> EventWebhook:
        current = await self.webhook()
        # Never overwrite another consumer, or silently rebind a saved Provider to another project.
        if current is not None and (current.url != url or existing_id not in (None, current.id)):
            raise ConnectorProviderError("webhook_owned_elsewhere")
        if current is None:
            if existing_id is not None or rotate:
                raise ConnectorProviderError("webhook_identity_changed")
            return _webhook(
                await self._request(
                    "POST",
                    "/webhook_subscriptions",
                    body={"webhook_url": url, "enabled_events": ["composio.trigger.message"], "version": "V3"},
                )
            )
        if rotate:
            value = await self._request("POST", f"/webhook_subscriptions/{path_segment(current.id)}/rotate_secret")
            if value.get("id") != current.id:
                raise ConnectorProviderError("provider_mismatch", outcome_unknown=True)
            return current.model_copy(update={"secret": SecretStr(required_string(value, "secret"))})
        return current

    async def instances(self, account_id: str, event_type: str) -> list[EventInstance]:
        items = await directory_items(
            self.http,
            endpoint=COMPOSIO_ENDPOINT,
            api_key=self.api_key,
            path="/api/v3.1/trigger_instances/active?"
            + urlencode({"connected_account_ids": account_id, "trigger_names": event_type, "show_disabled": "true"}),
            budget=DirectoryBudget(),
            page_size=50,
        )
        result = []
        for item in items:
            if item.get("connected_account_id") != account_id or item.get("trigger_name") != event_type:
                raise ConnectorProviderError("provider_mismatch")
            result.append(
                EventInstance(
                    id=required_string(item, "id"),
                    account_id=account_id,
                    user_id=required_string(item, "user_id"),
                    event_type=event_type,
                    config=required_object(item["trigger_config"]),
                    disabled=item.get("disabled_at") is not None,
                    version=required_string(item, "version"),
                    destination=optional_string(item.get("egress_url")),
                )
            )
        return result

    async def upsert(self, definition: EventType, account_id: str, user_id: str, config: JsonObject) -> str:
        value = await self._request(
            "POST",
            f"/trigger_instances/{path_segment(definition.key)}/upsert",
            body={
                "connected_account_id": account_id,
                "user_id": user_id,
                "trigger_config": config,
                "toolkit_versions": {definition.app: definition.version} if definition.version else "latest",
            },
        )
        return required_string(value, "trigger_id")

    async def transition(self, instance_id: str, state: str) -> None:
        path = f"/trigger_instances/manage/{path_segment(instance_id)}"
        if state == "retired":
            try:
                await self.http.request(
                    "DELETE", endpoint=COMPOSIO_ENDPOINT, path="/api/v3.1" + path, api_key=self.api_key, write=True
                )
            except ConnectorProviderError as error:
                if error.http_status != 404:
                    raise
        else:
            await self._request("PATCH", path, body={"status": "enable" if state == "enabled" else "disable"})
