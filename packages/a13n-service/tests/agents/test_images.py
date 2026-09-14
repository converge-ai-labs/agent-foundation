from io import BytesIO
from pathlib import Path

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import CreateAgentRequest, UpdateAgentRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.images import image_key
from a13n_service.etags import resource_etag
from a13n_service.object_retention.ownership import retained_owner
from a13n_service.profile_images import write_image
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.temporal import utc_now
from PIL import Image

from .conftest import ORG_ID, WORKSPACE_ID, actor, agent_config


@pytest.mark.anyio
async def test_avatar_publication_rechecks_etag_and_retains_only_current_image(
    agent_management: AgentManagement, agent_sessions, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="image-retention",
        request=CreateAgentRequest(name="Portrait", config=agent_config()),
    )
    objects = await LocalObjectStore.create(tmp_path / "images")
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(buffer, format="PNG")
    uploaded = await agent_management.images.replace(
        actor=actor(),
        agent_id=created.agent.id,
        content=buffer.getvalue(),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
        objects=objects,
    )
    assert uploaded.image_url
    image_id = uploaded.image_url.split("/")[-1]
    current_key = image_key(ORG_ID, WORKSPACE_ID, uploaded.id, image_id)
    async with short_session(agent_sessions) as session:
        assert await retained_owner(session, current_key, now=utc_now()) is True
        assert (
            await retained_owner(session, image_key("org_other", WORKSPACE_ID, uploaded.id, image_id), now=utc_now())
            is False
        )
    orphan_keys: list[str] = []

    async def race(objects, key: str, content: bytes) -> None:
        await write_image(objects, key, content)
        orphan_keys.append(key)
        await agent_management.commands.patch_metadata(
            actor=actor(),
            agent_id=uploaded.id,
            if_match=resource_etag(uploaded.id, uploaded.updated_at),
            request=UpdateAgentRequest(name="Changed concurrently"),
        )

    monkeypatch.setattr("a13n_service.agents.images.write_image", race)
    with pytest.raises(AgentError, match="representation has changed"):
        await agent_management.images.replace(
            actor=actor(),
            agent_id=uploaded.id,
            content=buffer.getvalue(),
            if_match=resource_etag(uploaded.id, uploaded.updated_at),
            objects=objects,
        )
    current = await agent_management.queries.get(actor=actor(), agent_id=uploaded.id)
    assert current.image_url == uploaded.image_url
    async with short_session(agent_sessions) as session:
        assert await retained_owner(session, current_key, now=utc_now()) is True
        assert await retained_owner(session, orphan_keys[0], now=utc_now()) is False
    removed = await agent_management.images.replace(
        actor=actor(),
        agent_id=current.id,
        content=None,
        if_match=resource_etag(current.id, current.updated_at),
        objects=objects,
    )
    assert removed.image_url is None
    async with short_session(agent_sessions) as session:
        assert await retained_owner(session, current_key, now=utc_now()) is False
