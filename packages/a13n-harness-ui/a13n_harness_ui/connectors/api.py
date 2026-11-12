"""Instance-authenticated thin Connector HTTP adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from pydantic import Field, JsonValue

from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorRule

from .models import CatalogItem, Instance, SetupCommand, SetupOffer, SetupSnapshot, Value

if TYPE_CHECKING:
    from a13n_harness_ui.app import HarnessUiApp


class Save(Value):
    kind: str
    name: str = Field(default="", max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    enabled: bool = True
    deliver_to_bot: bool = False
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    rules: tuple[ConnectorRule, ...] = ()


class Delete(Value):
    expected_version: int = Field(ge=1)


class Begin(Value):
    intent: str = "configure"


def register_connector_routes(server: FastAPI, get_app: Callable[[], HarnessUiApp]) -> None:
    from a13n_harness_ui.webui import _body, _document

    @server.get("/api/connectors/catalog", response_model=tuple[CatalogItem, ...])
    async def connector_catalog() -> tuple[CatalogItem, ...]:
        return tuple(get_app().connectors().catalog.items.values())

    @server.get("/api/connectors", response_model=tuple[Instance, ...])
    async def connector_instances() -> tuple[Instance, ...]:
        return await get_app().connectors().instances()

    @server.get("/api/connectors/{connector_id}", response_model=Instance)
    async def connector_instance(connector_id: str) -> Instance:
        return await get_app().connectors().instance(connector_id)

    @server.put("/api/connectors/{connector_id}", response_model=Instance, openapi_extra=_body(Save))
    async def save_connector(connector_id: str, request: Request) -> Instance:
        command = await _document(request, Save)
        configuration = ConnectorConfiguration(
            connector_id=connector_id,
            kind=command.kind,
            name=command.name,
            enabled=command.enabled,
            deliver_to_bot=command.deliver_to_bot,
            settings=command.settings,
            rules=command.rules,
        )
        return await get_app().configure_connector(configuration, expected_version=command.expected_version)

    @server.delete("/api/connectors/{connector_id}", status_code=204, openapi_extra=_body(Delete))
    async def delete_connector(connector_id: str, request: Request) -> Response:
        command = await _document(request, Delete)
        await get_app().delete_connector(connector_id, expected_version=command.expected_version)
        return Response(status_code=204)

    @server.post("/api/connectors/{connector_id}/setup", response_model=SetupSnapshot, openapi_extra=_body(Begin))
    async def begin_connector_setup(connector_id: str, request: Request) -> SetupSnapshot:
        command = await _document(request, Begin)
        return await get_app().begin_connector_setup(connector_id, intent=command.intent)

    @server.get("/api/connector-setups/{session_id}", response_model=SetupSnapshot)
    async def connector_setup(session_id: str) -> SetupSnapshot:
        return await get_app().connectors().setup(session_id)

    @server.post(
        "/api/connector-setups/{session_id}/actions", response_model=SetupSnapshot, openapi_extra=_body(SetupCommand)
    )
    async def connector_setup_action(session_id: str, request: Request) -> SetupSnapshot:
        return await get_app().act_connector_setup(session_id, await _document(request, SetupCommand))

    @server.get("/api/threads/{thread_id}/connector-setup-offers", response_model=tuple[SetupOffer, ...])
    async def connector_setup_offers(thread_id: str) -> tuple[SetupOffer, ...]:
        return await get_app().connectors().store.connectors.offers(thread_id)
