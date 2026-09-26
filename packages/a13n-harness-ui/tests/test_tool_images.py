from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from a13n_harness import HarnessEvent
from a13n_harness_ui.thread_files import ThreadFiles
from a13n_harness_ui.thread_projection import _request_parts
from a13n_harness_ui.tool_images import ToolImageCollector, tool_image_unavailable, tool_images
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import FunctionToolResultEvent, ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart

pytestmark = pytest.mark.anyio


def screenshot_event(*, valid: bool = True, run_id: str = "run-one", exposed: bool = True):
    buffer = BytesIO()
    Image.new("RGB", (20, 10), "blue").save(buffer, format="PNG")
    media = BinaryContent(
        data=buffer.getvalue() if valid else b"bad-image", media_type="image/png", vendor_metadata={"display": False}
    )
    part = ToolReturnPart(
        "computer_observe",
        {"ok": True, "observation_id": "obs-one"},
        "call-one",
        metadata={"a13n.computer.screenshot": exposed},
    )
    event = FunctionToolResultEvent(part, content=[media])
    return HarnessEvent(thread_id="thread-one", run_id=run_id, sequence=1, occurred_at=datetime.now(UTC), event=event)


async def test_tool_image_is_retained_and_round_trips_without_binary_json(tmp_path: Path):
    files = ThreadFiles(tmp_path)
    collector = ToolImageCollector(run_id="run-one", thread_id="thread-one", files=files)
    item = screenshot_event()
    events = await collector.observe(item)
    assert len(events) == 1 and events[0].name == "a13n.harness-ui.tool_images"
    part = item.event.part
    (image,) = tool_images(part)
    assert (await files.read("thread-one", image.attachment.attachment_id))[1] == item.event.content[0].data
    assert part.content == {"ok": True, "observation_id": "obs-one"}
    raw = ModelMessagesTypeAdapter.dump_json([ModelRequest(parts=[part])])
    assert b"base64" not in raw
    restored = ModelMessagesTypeAdapter.validate_json(raw)[0].parts[0]
    projected = _request_parts(restored)[0]
    assert projected.tool_images == (image,)
    assert not projected.tool_image_unavailable
    assert item.event.content[0].vendor_metadata == {"display": False}
    await files.close()
    reopened = ThreadFiles(tmp_path)
    assert (await reopened.read("thread-one", image.attachment.attachment_id))[0] == image.attachment
    await reopened.close()


async def test_tool_image_retention_failure_does_not_fail_model_result(tmp_path: Path):
    files = ThreadFiles(tmp_path)
    collector = ToolImageCollector(run_id="run-one", thread_id="thread-one", files=files)
    item = screenshot_event(valid=False)
    events = await collector.observe(item)
    assert events and tool_image_unavailable(item.event.part)
    assert tool_images(item.event.part) == ()
    assert item.event.part.content["ok"] is True
    assert not await collector.observe(screenshot_event(run_id="other"))
    assert not await collector.observe(screenshot_event(exposed=False))
    await files.close()
