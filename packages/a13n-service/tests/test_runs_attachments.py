"""Attachments: how assets and URL content reach the model, as message content or as files of the primary
environment, and the refusal of a file a run could not read."""

import asyncio
import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest
from a13n_service.infra.db import short_session
from a13n_service.runs.attachments import INLINE_TEXT_BYTES
from a13n_service.runs.checkpoints import StatePointer, load_state
from a13n_service.runs.tables import RunRow
from a13n_service.settings import LocalProvisioning, Provisioning, Settings
from fastapi import FastAPI
from fastapi.responses import Response

pytestmark = pytest.mark.anyio

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
ARCHIVE = b"PK\x03\x04 a fictional archive"
PDF = b"%PDF-1.7 a fictional document"
BLOCKED = b"PK\x03\x04 a fictional archive the environment refuses"


async def _model(service, scripted_model, *capabilities: str) -> str:  # type: ignore[no-untyped-def]
    provider = await service.client.post(
        f"{service.api}/model-providers",
        json={
            "type": "openai",
            "name": "Scripted",
            "config": {"base_url": scripted_model.url},
            "credential": {"api_key": "sk-scripted"},
        },
    )
    assert provider.status_code == 201, provider.text
    config = {
        "model_name": "scripted",
        "model_api": "openai.chat_completions",
        "characteristics": {"capabilities": list(capabilities)},
    }
    model = await service.client.post(
        f"{service.api}/models",
        json={"provider_id": provider.json()["id"], "key": "scripted", "name": "S", "config": config},
    )
    assert model.status_code == 201, model.text
    return model.json()["key"]


async def _asset(service, runs_kit, name: str, content_type: str, data: bytes) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    upload = await service.client.post(
        f"{service.api}/uploads", files={"file": (name, data, content_type)}, headers=runs_kit.fresh_key()
    )
    assert upload.status_code == 200, upload.text
    asset = await service.client.post(
        f"{service.api}/assets", json={"upload_id": upload.json()["upload_id"], "name": name}
    )
    assert asset.status_code == 201, asset.text
    return asset.json()


def _message(agent: dict[str, Any], *parts: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "agent_id": agent["id"],
        "payload": {"content": [{"type": "text", "text": "see attached"}, *parts]},
        **fields,
    }


def _attached(asset: dict[str, Any]) -> dict[str, Any]:
    return {"type": "asset", "asset_id": asset["id"]}


def _user_parts(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The content parts of the request's user messages, a plain string as a text part."""
    parts: list[dict[str, Any]] = []
    for message in request["messages"]:
        if message["role"] == "user":
            content = message["content"]
            parts.extend([{"type": "text", "text": content}] if isinstance(content, str) else content)
    return parts


def _user_texts(request: dict[str, Any]) -> list[str]:
    return [part["text"] for part in _user_parts(request) if part["type"] == "text"]


async def _local_template(service, root: Path) -> str:  # type: ignore[no-untyped-def]
    provider = await service.client.post(
        f"{service.api}/environment-providers", json={"type": "local", "name": "Local"}
    )
    assert provider.status_code == 201, provider.text
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Local",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"root": {"path": str(root)}}},
        },
    )
    assert template.status_code == 201, template.text
    return template.json()["id"]


def _with_local_environments(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "provisioning": Provisioning(
                local=LocalProvisioning(enabled=True, root=settings.objects.root.parent / "environments")
            )
        }
    )


async def test_the_model_reads_what_it_understands_natively_and_text_inline(
    service, scripted_model, runs_kit, listen
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    model = await _model(service, scripted_model, "image_understanding", "document_understanding")
    agent = await runs_kit.add_agent(service, "viewer", model)
    # Only the image types every provider maps are native: a TIFF is neither native nor text.
    tiff = await _asset(service, runs_kit, "scan.tiff", "image/tiff", b"II*\x00")
    refused = await service.client.post(
        f"{service.api}/threads", json=_message(agent, _attached(tiff)), headers=runs_kit.fresh_key()
    )
    assert refused.status_code == 400 and refused.json()["error"]["details"]["media_type"] == "image/tiff"

    image = await _asset(service, runs_kit, "swatch.png", "image/png", PNG)
    report = await _asset(service, runs_kit, "report.pdf", "application/pdf", PDF)
    notes = await _asset(service, runs_kit, "notes.csv", "text/csv", b"team,done\natlas,42\n")
    vector = await _asset(service, runs_kit, "logo.svg", "image/svg+xml", b"<svg/>")
    # URL text is decoded with its charset and, with no environment to place it in, inlined truncated.
    app = FastAPI()
    long = b"\xe9" * (INLINE_TEXT_BYTES + 1)
    app.get("/notes.txt")(lambda: Response(long, media_type="text/plain; charset=iso-8859-1"))
    async with listen(app) as origin:
        started = await service.client.post(
            f"{service.api}/threads",
            json=_message(
                agent,
                _attached(image),
                _attached(report),
                _attached(notes),
                _attached(vector),
                {"type": "url", "url": f"{origin}/notes.txt"},
            ),
            headers=runs_kit.fresh_key(),
        )
        assert started.status_code == 201, started.text
        run_id = started.json()["run"]["id"]
        scripted_model.say("Seen")
        await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
    images = [part["image_url"]["url"] for part in _user_parts(request) if part["type"] == "image_url"]
    assert images == [f"data:image/png;base64,{base64.b64encode(PNG).decode()}"]
    documents = [part["file"]["file_data"] for part in _user_parts(request) if part["type"] == "file"]
    assert documents == [f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}"]
    texts = _user_texts(request)
    assert 'Attachment "notes.csv" (text/csv, 19 bytes):\nteam,done\natlas,42\n' in texts
    assert 'Attachment "logo.svg" (image/svg+xml, 6 bytes):\n<svg/>' in texts
    truncated = (
        f'Attachment "notes.txt" (text/plain, {len(long)} bytes), truncated to its first {INLINE_TEXT_BYTES} bytes'
    )
    assert f"{truncated}:\n{'é' * INLINE_TEXT_BYTES}" in texts
    # Viewers see the attachments themselves, not the text the model reads for them.
    displayed = runs_kit.texts(await runs_kit.items(service, run_id))
    assert not [text for _, text in displayed if text.startswith("Attachment")], displayed


async def test_a_large_attachment_is_saved_once_and_read_again_by_the_next_run(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.add_agent(service, "reader", await _model(service, scripted_model, "document_understanding"))
    large = PDF + bytes(service.runtime.settings.worker.content_bytes)
    report = await _asset(service, runs_kit, "report.pdf", "application/pdf", large)
    started = await service.client.post(
        f"{service.api}/threads", json=_message(agent, _attached(report)), headers=runs_kit.fresh_key()
    )
    assert started.status_code == 201, started.text
    thread_id, run_id = started.json()["thread"]["id"], started.json()["run"]["id"]
    scripted_model.say("Read")
    await (await runs_kit.attempt(service))

    # The checkpoint references the document instead of holding it, and the sealed run keeps what it references.
    async with short_session(service.runtime.storage) as session:
        pointer = StatePointer.model_validate((await session.get_one(RunRow, run_id)).checkpoint)
    (ref,) = pointer.refs
    assert "/contents/" in ref.key and ref.size < len(large)
    assert await service.runtime.objects.get(ref.key) is not None
    state = await load_state(service.runtime.objects, pointer)
    assert state is not None and len(state.harness.model_dump_json()) < len(large)

    submitted = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "once more"))
    assert submitted.status_code == 201, submitted.text
    scripted_model.say("Again")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        assert StatePointer.model_validate(
            (await session.get_one(RunRow, submitted.json()["run"]["id"])).checkpoint
        ).refs == (ref,)
    document = f"data:application/pdf;base64,{base64.b64encode(large).decode()}"
    for request in (await scripted_model.request(), await scripted_model.request()):
        assert [part["file"]["file_data"] for part in _user_parts(request) if part["type"] == "file"] == [document]


async def test_a_file_the_run_cannot_read_is_refused_when_submitted_or_edited(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.add_agent(service, "reader", await _model(service, scripted_model))
    unreadable = {
        "application/zip": await _asset(service, runs_kit, "notes.zip", "application/zip", ARCHIVE),
        # The model declares no understanding, and a text larger than a text part is not read inline.
        "image/png": await _asset(service, runs_kit, "swatch.png", "image/png", PNG),
        "application/pdf": await _asset(service, runs_kit, "report.pdf", "application/pdf", PDF),
        "text/plain": await _asset(service, runs_kit, "long.txt", "text/plain", b"x" * (INLINE_TEXT_BYTES + 1)),
    }
    for media_type, asset in unreadable.items():
        refused = await service.client.post(
            f"{service.api}/threads", json=_message(agent, _attached(asset)), headers=runs_kit.fresh_key()
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["details"] == {
            "field": "content.1.asset_id",
            "reason": "environment_required",
            "media_type": media_type,
        }
    assert (await service.client.get(f"{service.api}/threads")).json()["items"] == []

    # A text file within a text part's size is read inline, whatever the run mounts.
    short = await _asset(service, runs_kit, "short.txt", "text/plain", b"x" * INLINE_TEXT_BYTES)
    thread_id = (await runs_kit.start_thread(service, agent, "first"))["thread"]["id"]
    queued = await runs_kit.submit(service, thread_id, _message(agent, _attached(short), delivery="next_run"))
    assert queued.status_code == 201, queued.text
    entry = f"{service.api}/threads/{thread_id}/inbox/{queued.json()['entry']['id']}"
    edit = {"payload": {"content": [_attached(unreadable["application/zip"])]}}
    thread = runs_kit.if_match(await runs_kit.get_thread(service, thread_id))
    edited = await service.client.patch(entry, json=edit, headers=thread)
    assert edited.status_code == 400 and edited.json()["error"]["details"]["field"] == "content.0.asset_id", edited.text


async def test_other_files_are_placed_in_the_primary_environment(
    serve, settings: Settings, scripted_model, runs_kit, listen, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """The model reads a reference to a file placed in the run's primary environment under a name that is one
    safe path segment. Offering the same bytes again, here in a steer, writes the file again only if its size
    changed, and a steer whose file the environment refuses fails alone."""
    app = FastAPI()
    app.get("/files/{name}")(lambda name: Response(b"\x00\x01binary", media_type="application/octet-stream"))
    async with listen(app) as origin, serve(settings=_with_local_environments(settings)) as service:
        await runs_kit.pause_sweeps(service)
        agent = await runs_kit.add_agent(
            service,
            "unpacker",
            await _model(service, scripted_model),
            toolsets={"configuration": {"enabled": True}},
            default_environment_template_id=await _local_template(service, tmp_path),
        )
        # A name longer than a file name may be is cut to 200 UTF-8 bytes, keeping its extension.
        archive = await _asset(service, runs_kit, "报告" * 60 + ".zip", "application/zip", ARCHIVE)
        name = ("报告" * 60)[:65] + ".zip"
        # The model does not declare document understanding, so a PDF is a file like any other.
        report = await _asset(service, runs_kit, "report.pdf", "application/pdf", PDF)
        blocked = await _asset(service, runs_kit, "blocked.zip", "application/zip", BLOCKED)
        gate = asyncio.Event()
        scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", gate=gate)
        scripted_model.say("Unpacked")
        # Control characters in a URL's last segment are left out of the name.
        url = {"type": "url", "url": f"{origin}/files/data%0A%00.bin"}
        started = await service.client.post(
            f"{service.api}/threads",
            json=_message(agent, _attached(archive), _attached(report), url),
            headers=runs_kit.fresh_key(),
        )
        assert started.status_code == 201, started.text
        run = started.json()["run"]
        [mount] = run["environment_mounts"]
        attachments = tmp_path / mount["environment_id"] / ".a13n" / "attachments"

        running = await runs_kit.attempt(service)
        first = await scripted_model.request()
        digest = hashlib.sha256(ARCHIVE).hexdigest()
        placed = attachments / digest / name
        assert placed.read_bytes() == ARCHIVE
        fetched = attachments / hashlib.sha256(b"\x00\x01binary").hexdigest() / "data.bin"
        assert fetched.read_bytes() == b"\x00\x01binary"
        document = attachments / hashlib.sha256(PDF).hexdigest() / "report.pdf"
        assert document.read_bytes() == PDF
        assert not [part for part in _user_parts(first) if part["type"] == "file"]
        reference = (
            f'Attachment "{name}" (application/zip, {len(ARCHIVE)} bytes), placed in the environment at: '
            f"/workspace/.a13n/attachments/{digest}/{name}"
        )
        assert reference in _user_texts(first)
        assert any(
            text.startswith('Attachment "data.bin" (application/octet-stream, 8 bytes)') for text in _user_texts(first)
        )

        # The agent changed its copy's size, and a directory stands where the blocked file would go.
        placed.write_bytes(b"changed by the agent")
        (attachments / hashlib.sha256(BLOCKED).hexdigest() / "blocked.zip").mkdir(parents=True)
        thread_id = run["thread_id"]
        again = await runs_kit.submit(service, thread_id, _message(agent, _attached(archive)))
        refused = await runs_kit.submit(service, thread_id, _message(agent, _attached(blocked)))
        assert again.status_code == 201 and refused.status_code == 201, (again.text, refused.text)
        gate.set()
        await running
        following = await scripted_model.request()
        assert (await runs_kit.get_run(service, run["id"]))["status"] == "completed"
        assert _user_texts(following).count(reference) == 2
        assert placed.read_bytes() == ARCHIVE
        assert [path.name for path in placed.parent.iterdir()] == [name]
        entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
        failed = entries[refused.json()["entry"]["id"]]
        assert failed["status"] == "failed" and failed["failure"]["code"] == "invalid_argument", failed
        assert entries[again.json()["entry"]["id"]]["status"] == "consumed"


async def test_acceptance_refuses_a_queued_file_its_run_could_not_read(
    serve, settings: Settings, scripted_model, runs_kit, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """A file accepted for a thread that mounted a primary environment fails in place if the mount is gone by the
    time its run would start."""
    async with serve(settings=_with_local_environments(settings)) as service:
        await runs_kit.pause_sweeps(service)
        agent = await runs_kit.add_agent(service, "reader", await _model(service, scripted_model))
        template_id = await _local_template(service, tmp_path)
        reserved = await service.client.post(f"{service.api}/environments", json={"template_id": template_id})
        assert reserved.status_code == 201, reserved.text
        mounts = [{"name": "workspace", "environment_id": reserved.json()["id"]}]
        started = await runs_kit.start_thread(service, agent, "first", environments=mounts)
        thread_id = started["thread"]["id"]
        archive = await _asset(service, runs_kit, "notes.zip", "application/zip", ARCHIVE)
        queued = await runs_kit.submit(service, thread_id, _message(agent, _attached(archive), delivery="next_run"))
        assert queued.status_code == 201, queued.text

        thread = runs_kit.if_match(await runs_kit.get_thread(service, thread_id))
        removed = await service.client.delete(
            f"{service.api}/threads/{thread_id}/environments/workspace", headers=thread
        )
        assert removed.status_code == 204, removed.text
        scripted_model.say("Done")
        await (await runs_kit.attempt(service))
        assert (await runs_kit.get_run(service, started["run"]["id"]))["status"] == "completed"

        (_, entry) = await runs_kit.inbox(service, thread_id)
        assert entry["status"] == "failed" and entry["failure"]["code"] == "invalid_argument", entry
        assert (await runs_kit.get_thread(service, thread_id))["current_run_id"] is None
