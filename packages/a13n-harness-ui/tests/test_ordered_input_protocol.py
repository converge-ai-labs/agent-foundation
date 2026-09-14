"""Ordered HTTP input reuses retained Thread bytes and native composer metadata."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after, sleep
from PIL import Image
from pydantic_ai.messages import BinaryContent, ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_interactive_protocol import listener

pytestmark = pytest.mark.anyio


async def test_ordered_submit_steer_and_restart_keep_identity_and_native_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_configuration(tmp_path)
    started, release = Event(), Event()
    observed = []

    async def model(messages, info):
        observed.extend(messages)
        started.set()
        await release.wait()
        yield "Received ordered input"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    image = BytesIO()
    Image.new("RGB", (3, 2), "red").save(image, format="PNG")
    headers = {"Authorization": "Bearer test-only-key"}
    source = tmp_path / "context.txt"
    source.write_text("captured instruction")
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=headers, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            upload = await api.post(prefix + "/attachments?name=red.png", content=image.getvalue())
            assert upload.status_code == 200, upload.text
            identity = upload.json()["attachment_id"]
            file = (await api.post(prefix + "/attachments?name=notes.txt", content=b"notes")).json()["attachment_id"]
            meta = (await api.get("/api/host/files/metadata", params={"path": str(source)})).json()
            captured = (
                await api.post(
                    prefix + "/host-file-captures",
                    json={
                        "path": str(source),
                        "expected_revision": meta["revision"],
                    },
                )
            ).json()["attachment"]["attachment_id"]
            source.write_text("changed after capture")
            other = (await api.post("/api/threads", json={})).json()["thread_id"]
            foreign = await api.post(f"/api/threads/{other}/submit", json={"parts": [{"attachment_id": identity}]})
            assert foreign.status_code == 400
            for body in (
                {"parts": []},
                {"parts": ["  "]},
                {"prompt": "ambiguous", "parts": ["ordered"]},
                {"attachment_ids": [identity], "parts": ["ordered"]},
                {"parts": [{"attachment_id": identity}] * 9},
                {"parts": ["a" * (128 * 1024 + 1), "b" * (128 * 1024)]},
            ):
                rejected = await api.post(prefix + "/submit", json=body)
                assert rejected.status_code == 400, rejected.text
            authored = [
                "Compare [image#1] literally ",
                {"attachment_id": identity},
                " then ",
                {"attachment_id": file},
                " and again ",
                {"attachment_id": identity},
                " end",
            ]
            response = await api.post(prefix + "/submit", json={"parts": authored})
            assert response.status_code == 200, response.text
            receipt = response.json()["receipt_id"]
            with fail_after(10):
                await started.wait()
            instruction = await api.post(
                f"/api/operations/{receipt}/steer",
                json={
                    "parts": ["Use ", {"attachment_id": captured}, " here"],
                },
            )
            assert instruction.status_code == 200 and instruction.json()["accepted"], instruction.text
            for unsupported in (identity, file):
                rejected = await api.post(
                    f"/api/operations/{receipt}/steer",
                    json={
                        "parts": ["keep ", {"attachment_id": captured}, {"attachment_id": unsupported}],
                    },
                )
                assert rejected.status_code == 400, rejected.text
                assert rejected.json()["error"]["code"] == "steer_context_unsupported"
            release.set()
            with fail_after(10):
                while (outcome := (await api.get(f"/api/operations/{receipt}")).json())["status"] in {
                    "preparing",
                    "running",
                }:
                    await sleep(0.02)
            assert outcome["status"] == "completed", outcome
            transcript = (await api.get(prefix + "/transcript")).json()
            parts = [
                part for entry in transcript["entries"] for part in entry["parts"] if part["kind"] in {"user", "media"}
            ]
            submitted = [
                part
                for part in parts
                if part["metadata"].get("harness_ui", {}).get("attachment", {}).get("attachment_id") == identity
            ]
            assert {part["metadata"]["harness_ui"]["composer"]["index"] for part in submitted} == {1, 5}
            source_ids = {part["metadata"]["source_id"] for part in submitted}
            assert len(source_ids) == 1
            assert "changed after capture" not in str(transcript)
            assert "captured instruction" in str(transcript)
            data = await api.get(prefix + f"/attachments/{identity}")
            assert data.content == image.getvalue()
    contents = [
        item
        for message in observed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
        for item in part.content
    ]
    native = [
        item.data if isinstance(item, BinaryContent) else item.content
        for item in contents
        if isinstance(item, BinaryContent)
        or (
            isinstance(item, TextContent)
            and item.content in {"Compare [image#1] literally ", " then ", " and again ", " end"}
        )
    ]
    assert native[:6] == [
        "Compare [image#1] literally ",
        image.getvalue(),
        " then ",
        " and again ",
        image.getvalue(),
        " end",
    ]
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=headers, trust_env=False) as api:
            assert (await api.get(prefix + f"/attachments/{identity}")).content == image.getvalue()
            restored = (await api.get(prefix + "/transcript")).json()
            assert restored == transcript
