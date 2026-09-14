"""Case 25: actual Asset input bytes, explicit output publication and denied access."""

import base64
import hashlib

import pytest

from ..infrastructure.management_packages import upload
from ..infrastructure.management_support import has_tool

pytestmark = pytest.mark.anyio
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


@pytest.mark.parametrize("kind", ["file", "image"])
async def test_uploaded_asset_reaches_harness_with_identical_bytes(management, kind):
    journey, live = management, management.live
    data = PNG if kind == "image" else b"LIVE_ASSET_BYTES_7f84af\n"
    media_type = "image/png" if kind == "image" else "text/plain"
    asset = await upload(
        journey,
        "assets",
        data,
        params={"filename": "proof.png" if kind == "image" else "proof.txt", "media_type": media_type},
    )
    assert asset["content_sha256"] == hashlib.sha256(data).hexdigest()
    environment, root = await journey.environment()
    case = await journey.case(steps=[] if kind == "image" else [{"tool": "view", "materialized_input": True}])
    input_value = live.start_body(case)["input"]
    input_value["content"].append(
        {
            "type": "binary",
            "source": {"type": "asset", "asset_id": asset["id"]},
            "delivery": "model_content" if kind == "image" else "environment_path",
        }
    )
    receipt = await journey.start(
        case,
        input=input_value,
        environment={"environment_id": environment["id"]},
        config_override={
            "model": {"characteristics": {"context_window": 32768, "capabilities": ["image_understanding"]}}
        },
    )
    result = await live.finish(receipt["run_id"])
    if kind == "image":
        images = [
            part["image_url"]["url"]
            for message in journey.observations(case)[0]["body"]["messages"]
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "image_url"
        ]
        assert len(images) == 1 and images[0].startswith("data:image/png;base64,")
        assert base64.b64decode(images[0].split(",", 1)[1]) == data
    else:
        assert data.decode().strip() in result["output_text"]
        materialized = list(root.rglob("content-1"))
        assert len(materialized) == 1 and materialized[0].read_bytes() == data
    content = await live.http.get(f"/api/v1/assets/{asset['id']}/content")
    assert content.status_code == 200 and content.content == data
    async with journey.outsider() as outsider:
        for suffix in ("", "/content"):
            denied = await outsider.get(f"/api/v1/assets/{asset['id']}" + suffix)
            assert denied.status_code in {403, 404} and data not in denied.content
    await journey.lab.stop(journey.lab.workers[-1])
    later = await journey.case()
    input_value["content"][0] = live.start_body(later)["input"]["content"][0]
    pending = await journey.start(
        later,
        input=input_value,
        environment={"environment_id": environment["id"]},
        config_override={"model": {"characteristics": {"capabilities": ["image_understanding"]}}},
    )
    deleted = await live.http.delete(f"/api/v1/assets/{asset['id']}")
    assert deleted.status_code == 204
    await journey.lab.start_worker()
    await live.finish(pending["run_id"], "failed")
    assert journey.observations(later) == [], "Deleted Asset bytes crossed the model boundary"


async def test_output_publication_is_explicit_immutable_and_run_linked(management):
    journey, live = management, management.live
    environment, root = await journey.environment()
    selection = {"environment_id": environment["id"]}
    plain = await journey.agent()
    case = await journey.case()
    receipt = await journey.start(case, agent_id=plain["agent"]["id"], environment=selection)
    await live.finish(receipt["run_id"])
    assert not has_tool(journey.observations(case)[0], "publish_asset")
    enabled = await journey.agent(toolsets={"assets": {"enabled": True, "tools": {"publish": {"permission": "allow"}}}})
    data = "GENERATED_THROUGH_HARNESS"
    case = await journey.case(
        steps=[
            {"tool": "write", "arguments": {"file_path": "/workspace/generated.txt", "content": data}},
            {
                "tool": "publish_asset",
                "publication_path": "/workspace/generated.txt",
                "arguments": {"media_type": "text/plain"},
            },
        ]
    )
    receipt = await journey.start(case, agent_id=enabled["agent"]["id"], environment=selection)
    result = await live.finish(receipt["run_id"])
    assets = await live.collection(journey.base + "/assets")
    assert len(assets) == 1
    asset = assets[0]
    assert asset["source"]["kind"] == "run_output" and asset["source"]["run_id"] == result["id"]
    assert asset["id"] in result["output_text"]
    assert asset["content_sha256"] == hashlib.sha256(data.encode()).hexdigest()
    (root / "generated.txt").write_text("CHANGED_AFTER_PUBLICATION")
    downloaded = await live.http.get(f"/api/v1/assets/{asset['id']}/content")
    assert downloaded.status_code == 200 and downloaded.content == data.encode()
    async with journey.outsider() as outsider:
        denied = await outsider.get(f"/api/v1/assets/{asset['id']}/content")
        assert denied.status_code in {403, 404} and data not in denied.text
    assert await live.request("GET", f"/api/v1/assets/{asset['id']}") == asset
