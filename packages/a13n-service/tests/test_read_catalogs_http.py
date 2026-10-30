"""Catalog indexes and definitions reuse provider discovery and workspace authorization."""

import pytest
from a13n_service.resources.models.schemas import ModelCatalog

from .test_connections import COMPOSIO_KEY, Composio, create, remote, serve
from .test_models_catalog import one

pytestmark = pytest.mark.anyio


async def test_connection_index_definitions_and_cross_workspace_refusal(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(extra_tools=25) as server:
        connection = await create(service, {"config": {"url": f"{server.url}/mcp"}})
        path = service.api + f"/connections/{connection['id']}/tools"
        original = await service.client.get(path)
        assert original.status_code == 200
        cursor, found = None, []
        while True:
            params = {"limit": 7}
            if cursor:
                params["cursor"] = cursor
            response = await service.client.get(path + "/index", params=params)
            assert response.status_code == 200 and "input_schema" not in response.text, response.text
            body = response.json()
            found.extend(item["name"] for item in body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                break
        assert found == sorted(item["name"] for item in original.json()["items"])
        selected = await service.client.get(path + "/definitions", params=[("name", "ping"), ("name", "missing")])
        assert selected.status_code == 200, selected.text
        assert selected.json()["items"][0]["definition"] == next(
            item for item in original.json()["items"] if item["name"] == "ping"
        )
        assert selected.json()["missing"] == ["missing"]
        refused = await service.client.get(
            path + "/definitions", params={"name": "ping"}, headers={"X-Workspace-ID": "ws_" + "f" * 20}
        )
        assert refused.status_code in {403, 404}, refused.text


async def test_connector_action_index_and_selected_versioned_definition(service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from a13n_harness.providers.connector.composio import catalog, runtime

    fake = Composio()
    async with serve(fake.app) as url:
        fake.url = url
        for module in (runtime, catalog):
            monkeypatch.setattr(module, "COMPOSIO_ENDPOINT", url)
        response = await service.client.post(
            service.api + "/connector-providers",
            json={"type": "composio", "name": "Composio", "credential": {"api_key": COMPOSIO_KEY}},
        )
        assert response.status_code == 201, response.text
        path = service.api + f"/connector-providers/{response.json()['id']}/apps/github/actions"
        full = await service.client.get(path)
        index = await service.client.get(path + "/index", params={"query": "github"})
        assert index.status_code == 200 and index.json()["items"][0]["name"] == "GITHUB_CREATE", index.text
        assert index.json()["items"][0]["provider_version"] == full.json()["items"][0]["provider_version"]
        detail = await service.client.get(path + "/definitions", params={"name": "GITHUB_CREATE"})
        assert detail.status_code == 200 and detail.json()["items"][0]["definition"] == full.json()["items"][0], (
            detail.text
        )


async def test_model_compact_filters_paging_and_single_model_sections(service) -> None:  # type: ignore[no-untyped-def]
    model = one({"input": 5, "output": 10})
    values = [
        model.model_copy(update={"ref": model.ref.model_copy(update={"model": f"model{i:02}"}), "name": f"Model {i}"})
        for i in range(23)
    ]

    class Catalog:
        async def read(self, *, provider=None, model=None):  # type: ignore[no-untyped-def]
            return ModelCatalog(
                status="ready",
                items=[
                    value
                    for value in values
                    if (provider is None or value.ref.provider == provider)
                    and (model is None or value.ref.model == model)
                ],
            )

    service.app.state.model_catalog = Catalog()
    full = await service.client.get("/api/v1/model-catalog")
    assert full.status_code == 200 and len(full.json()["items"]) == 23 and "next_cursor" not in full.json()
    cursor, found = None, []
    while True:
        params = {"view": "compact", "capability": "image_understanding", "limit": 8}
        if cursor:
            params["cursor"] = cursor
        response = await service.client.get("/api/v1/model-catalog", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 8 and "pricing" not in body["items"][0]
        found.extend(value["ref"]["model"] for value in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert found == [value.ref.model for value in values]
    selected = await service.client.get(
        "/api/v1/model-catalog", params={"provider": model.ref.provider, "model": "model00", "section": "pricing"}
    )
    assert selected.status_code == 200 and selected.json()["representation"] == "fragment", selected.text
    refused = await service.client.get("/api/v1/model-catalog", params={"section": "pricing"})
    assert refused.status_code == 400
