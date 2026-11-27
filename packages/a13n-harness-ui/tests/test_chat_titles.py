from contextlib import asynccontextmanager
from gzip import compress
from io import BytesIO
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness import ContentItem, ContentMetadata, HarnessState, RunConfiguration
from a13n_harness.content import annotate_prompt
from a13n_harness.image_input import ImageInputPolicy
from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability
from a13n_harness_ui.app import AppState, open_harness_ui_app
from a13n_harness_ui.chat_titles import (
    TitleAttachment,
    TitleMessage,
    _sample,
    _samples,
    generate_title,
    title_message_from_entry,
    title_message_from_prompt,
)
from a13n_harness_ui.skill_input import prepare_skill_input
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.storage.runtime import open_local_store
from a13n_harness_ui.surfaces import (
    RootOperationStatus,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
    TranscriptEntry,
    TranscriptPart,
)
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerAttachment, ComposerInput
from anyio import (
    CancelScope,
    Event,
    create_task_group,
    fail_after,
    get_cancelled_exc_class,
    move_on_after,
    sleep,
    sleep_forever,
)
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from .test_app import _CompletedReconstructor, _settings, _write_configuration
from .test_skill_input import catalog


def _captured_title_model(calls, characteristics=None):
    def response(messages, info):
        prompt = next(part for part in messages[-1].parts if isinstance(part, UserPromptPart))
        calls.append((prompt.content, info.instructions))
        return ModelResponse(parts=[TextPart("Generated title")])

    return FunctionModel(response), SimpleNamespace(settings={}, model_characteristics=characteristics)


def _prompt_text(content):
    return "\n".join(item for item in content if isinstance(item, str))


@pytest.mark.parametrize(("budget", "expected"), [(-1, ""), (0, ""), (1, "one"), (2, "one two")])
def test_title_sampling_respects_tiny_budgets(budget, expected):
    assert _sample("  one two three four  ", budget) == expected


def test_attachment_with_whitespace_text_does_not_dilute_title_budget():
    message = TitleMessage("user", "word " * 2500)
    attachment_only = TitleMessage("user", " \t\n", attachments=(TitleAttachment("note.txt", "text/plain"),))
    assert _samples([message, attachment_only]) == [*_samples([message]), ""]


@pytest.mark.anyio
async def test_title_samples_both_ends_with_one_shared_budget():
    calls = []
    model, recipe = _captured_title_model(calls)
    messages = [
        TitleMessage("user", "start " + "middle " * 3000 + "end"),
        TitleMessage("user", "开始" + "词" * 3000 + "结束"),
    ]
    await generate_title(messages, model, recipe)
    first, last = _prompt_text(calls[0][0]).split("\n\n")
    first, last = first.removeprefix("User: "), last.removeprefix("User: ")
    assert first.startswith("start ") and first.endswith(" end")
    assert len(first.split()) == 1000
    assert last.startswith("开始") and last.endswith("结束")
    assert len(last.replace(" ", "")) == 1000


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("rename", [False, True])
async def test_async_first_title_and_explicit_regeneration(tmp_path, monkeypatch, rename):
    root = _write_configuration(tmp_path)
    model_id = "model-title" if rename else "model-primary"
    if rename:
        primary = root.parent / "models/primary.yaml"
        primary.with_name("title.yaml").write_text(primary.read_text().replace("model-primary", model_id))
        root.write_text(root.read_text() + "chat_title_model: model-title\n")
    calls = []
    started, release = Event(), Event()
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:

        async def generate(thread_id, messages, source, model_id):
            calls.append((messages, model_id))
            started.set()
            await release.wait()
            return "Suggested title"

        monkeypatch.setattr(app, "_generate_chat_title", generate)
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="First topic")
        with fail_after(10):
            await started.wait()
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        thread = (await app.get_thread(thread_id)).thread
        if rename:
            await app.update_thread_metadata(
                thread_id=thread_id,
                mutation=ThreadMetadataMutation(
                    expected_version=thread.metadata_version, patch=ThreadMetadataPatch(title="My title")
                ),
            )
        release.set()
        if not rename:
            with fail_after(5):
                while (await app.get_thread(thread_id)).thread.title != "Suggested title":
                    await sleep(0.02)
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Second topic")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert await app.suggest_chat_title(thread_id) == "Suggested title"
        assert calls == [
            ([TitleMessage("user", "First topic")], model_id),
            (
                [
                    TitleMessage("user", "First topic"),
                    TitleMessage("assistant", "root complete"),
                    TitleMessage("user", "Second topic"),
                    TitleMessage("assistant", "root complete"),
                ],
                model_id,
            ),
        ]
        assert (await app.get_thread(thread_id)).thread.title == ("My title" if rename else "Suggested title")


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("cancel_lifetime", [False, True])
@pytest.mark.parametrize("stage", ["model", "publication"])
async def test_pending_automatic_title_joins_app_shutdown_after_root_completion(
    tmp_path, monkeypatch, cancel_lifetime, stage
):
    root = _write_configuration(tmp_path)
    held, release, cancelled, ready, stop, closed = (Event() for _ in range(6))
    owners, scopes = [], []
    metadata_writes = []
    cancelled_before_storage_close = []
    storage_closing = Event()

    @asynccontextmanager
    async def tracked_store(settings):
        async with open_local_store(settings) as store:
            try:
                yield store
            finally:
                storage_closing.set()

    monkeypatch.setattr("a13n_harness_ui.app.open_local_store", tracked_store)

    async def lifetime():
        with CancelScope() as scope:
            scopes.append(scope)
            async with open_harness_ui_app(
                _settings(tmp_path / "state", shutdown_timeout_seconds=0.02),
                configuration_path=root,
                host_mode="webui",
            ) as app:
                owners.append(app)

                async def wait_for_title():
                    held.set()
                    try:
                        await release.wait()
                    except get_cancelled_exc_class():
                        cancelled.set()
                        cancelled_before_storage_close.append(not storage_closing.is_set())
                        raise

                async def model(messages, info):
                    if stage == "model":
                        await wait_for_title()
                    return ModelResponse(parts=[TextPart("Generated title")])

                async def utility_model(source, model_id, thread_id):
                    return FunctionModel(model), SimpleNamespace(settings={}, model_characteristics=None)

                update = app.update_thread_metadata

                async def publish(**kwargs):
                    if stage == "publication":
                        await wait_for_title()
                    metadata_writes.append(kwargs)
                    return await update(**kwargs)

                monkeypatch.setattr(app, "_utility_model", utility_model)
                monkeypatch.setattr(app, "update_thread_metadata", publish)
                app._root_runs._executor._agents = _CompletedReconstructor()
                thread_id = (await app.create_thread()).thread_id
                receipt = await app.submit_thread(thread_id=thread_id, prompt="First topic")
                with fail_after(5):
                    await held.wait()
                    result = await app.wait_root_operation(receipt.receipt_id)
                assert result.status is RootOperationStatus.completed
                assert await app._root_runs.active_count() == 0
                assert (await app.get_thread(thread_id)).thread.title is None
                ready.set()
                await stop.wait()
        closed.set()

    with fail_after(15):
        async with create_task_group() as group:
            group.start_soon(lifetime)
            await ready.wait()
            if cancel_lifetime:
                scopes[0].cancel()
            else:
                stop.set()
            # Bound title cancellation, not unrelated collaborator/storage teardown.
            # Release the old implementation's shielded join if cancellation fails.
            with move_on_after(5) as shutdown_wait:
                await cancelled.wait()
            release.set()
            await closed.wait()
    assert not shutdown_wait.cancel_called
    assert cancelled.is_set()
    assert cancelled_before_storage_close == [True]
    assert metadata_writes == []
    app = owners[0]
    assert app.state is AppState.closed
    assert storage_closing.is_set()
    assert app._operations_idle.is_set()
    assert not app._operation_scopes
    assert app._root_runs._task_group is None


@pytest.mark.anyio
async def test_title_skills_are_selected_metadata_only_and_allow_legacy_entries():
    selected = prepare_skill_input("/review the patch; compare $HOME and /tmp", catalog(), ("review",))
    message = title_message_from_prompt(selected)
    legacy = TranscriptEntry(
        position=0,
        message_kind="request",
        parts=(
            TranscriptPart(
                kind="user",
                text="<skill-selection>generated instructions</skill-selection>",
                metadata=ContentMetadata.from_native(
                    {
                        "display": False,
                        "harness_ui": {"selected_skills": [{"name": "review"}, {"name": "test"}]},
                    }
                ),
            ),
        ),
    )

    async def unexpected_read(_):
        pytest.fail("Skill hints should never read Skill files")

    legacy_message = await title_message_from_entry(legacy, unexpected_read)
    assert legacy_message.has_input and not legacy_message.text
    assert not title_message_from_prompt("$HOME /tmp").skills
    calls = []
    model, recipe = _captured_title_model(calls)
    await generate_title([message, legacy_message], model, recipe)
    content, instructions = calls[0]
    text = _prompt_text(content)
    assert "User: /review the patch; compare $HOME and /tmp" in text
    assert "<skills>\n- review: Selected workflow\n- test\n</skills>" in text
    assert "generated instructions" not in text
    assert "invoked workflows ($name or leading /name)" in instructions


class _Stream(httpx2.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def _mock_title_http(monkeypatch, handler):
    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx2.MockTransport(handler))
    )


@pytest.mark.anyio
async def test_url_hints_prefer_user_urls_and_stop_at_head_boundary(monkeypatch):
    first = _Stream(
        b'<html><head><title>HTML title</title><meta name="twitter:title" content="Social title">',
        b'<meta property="og:title" cont',
        b'ent=" Open &amp; Graph "><meta name="description" content="Default">'
        b'<meta property="og:description" content="Specific info"></head><body><title>Ignore me</title>',
        b"never streamed",
    )
    second = _Stream(
        b"<head><title>Pair (example)</title><svg><title>Icon label</title></svg>"
        b'<meta name="description" content="PAIR (EXAMPLE)">'
        b'<body><meta property="og:title" content="Untrusted body">',
        b"never streamed",
    )
    third = _Stream(b"<head><title>Under limit</title>" + b" " * (48 * 1024), b"<title>Over limit</title></head>")
    streams = {"/one": first, "/pair(one)": second, "/large": third}
    requested = []

    async def handler(request):
        path = request.url.path
        requested.append(path)
        assert request.extensions["timeout"]["read"] == 10.0
        return httpx2.Response(
            200, headers={"Content-Type": "Application/XHTML+XML; charset=utf-8"}, stream=streams[path]
        )

    _mock_title_http(monkeypatch, handler)
    calls = []
    model, recipe = _captured_title_model(calls)
    await generate_title(
        [
            TitleMessage("assistant", "https://example.test/not-preferred"),
            TitleMessage(
                "user",
                "(https://example.test/one), https://example.test/one#anchor; "
                "[pair](https://example.test/pair(one)). 看这个\uff08https://example.test/large\uff09",
            ),
        ],
        model,
        recipe,
    )
    assert sorted(requested) == ["/large", "/one", "/pair(one)"]
    assert [first.read, second.read, third.read] == [3, 1, 1]
    assert all(stream.closed for stream in streams.values())
    text = _prompt_text(calls[0][0])
    assert '<url-hints trust="untrusted-reference-metadata">\n' in text
    assert "https://example.test/one: Open &amp; Graph — Specific info" in text
    assert "https://example.test/pair(one): Pair (example)\n" in text
    assert "https://example.test/large: Under limit\n" in text
    assert not any(
        value in text
        for value in ("Untrusted body", "Over limit", "PAIR (EXAMPLE)", "Default", "Social title", "Icon label")
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/pdf", ["/pdf"]),
        ("/error", ["/error"]),
        ("/wall", ["/wall"]),
        ("/client-challenge", ["/client-challenge"]),
        ("/must-login", ["/must-login"]),
        ("/denied-redirect", ["/denied-redirect"]),
        ("/userinfo-redirect", ["/userinfo-redirect"]),
        ("/0", ["/0", "/1", "/2", "/3"]),
        ("/slow", ["/slow"]),
    ],
)
async def test_url_hints_skip_unsafe_or_unhelpful_responses(monkeypatch, path, expected):
    requested = []
    unread = _Stream(b"<title>Do not read</title>")

    async def handler(request):
        path = request.url.path
        requested.append(path)
        match path:
            case "/pdf":
                return httpx2.Response(200, headers={"content-type": "application/pdf"}, stream=unread)
            case "/error":
                return httpx2.Response(404, headers={"content-type": "text/html"}, stream=unread)
            case "/wall":
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text='<title>Attention Required! | Cloudflare</title><meta property="og:title" content="Misleading">',
                )
            case "/client-challenge":
                return httpx2.Response(
                    200, headers={"content-type": "text/html"}, text="<title>Client Challenge</title>"
                )
            case "/must-login":
                return httpx2.Response(302, headers={"location": "/account/%73ign-in?next=/page"})
            case "/denied-redirect":
                return httpx2.Response(302, headers={"location": "https://other.test/restricted"})
            case "/userinfo-redirect":
                return httpx2.Response(302, headers={"location": "https://user:pass@example.test/private"})
            case "/slow":
                raise httpx2.ReadTimeout("mock timeout", request=request)
            case _:
                return httpx2.Response(302, headers={"location": f"/{int(path[1:]) + 1}"})

    _mock_title_http(monkeypatch, handler)
    calls = []
    model, recipe = _captured_title_model(calls)
    assert (
        await generate_title(
            [
                TitleMessage(
                    "user",
                    f"https://example.test{path} https://blocked.test/info",
                )
            ],
            model,
            recipe,
            configuration=RunConfiguration(allowed_hosts=frozenset({"example.test"})),
        )
        == "Generated title"
    )
    assert requested == expected
    assert not unread.read
    assert "<url-hints" not in _prompt_text(calls[0][0])


@pytest.mark.anyio
async def test_url_hints_keep_query_parameters_and_decode_gzip(monkeypatch):
    encoded = compress(b"<head><title>Build reference</title></head><body>Ignored</body>")
    stream = _Stream(encoded[:16], encoded[16:])
    targets = []

    async def handler(request):
        targets.append(str(request.url))
        assert request.headers["user-agent"] == "a13n-harness-ui"
        assert request.headers["accept"] == "text/html, application/xhtml+xml"
        assert "accept-encoding" not in request.headers
        if request.url.path == "/browse":
            return httpx2.Response(302, headers={"location": "/article?session=demo#section"})
        return httpx2.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"}, stream=stream)

    _mock_title_http(monkeypatch, handler)
    calls = []
    model, recipe = _captured_title_model(calls)
    await generate_title([TitleMessage("user", "Read https://example.test/browse?code=42&key=docs#top")], model, recipe)
    assert targets == ["https://example.test/browse?code=42&key=docs", "https://example.test/article?session=demo"]
    assert "https://example.test/browse?code=42&amp;key=docs: Build reference" in _prompt_text(calls[0][0])
    assert stream.read == 2 and stream.closed


@pytest.mark.anyio
async def test_url_hints_fetch_concurrently_without_redirect_cookies_and_keep_finished_hints(monkeypatch):
    stalled = Event()
    completed = Event()
    seen = []

    async def handler(request):
        path = request.url.path
        seen.append(path)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if path == "/slow":
            stalled.set()
            await sleep_forever()
        if path == "/redirect":
            await stalled.wait()
            return httpx2.Response(302, headers={"location": "/quick", "set-cookie": "sid=private; Path=/"})
        completed.set()
        return httpx2.Response(
            200,
            text='<title>Fallback</title><meta name="twitter:title" content="Finished">',
            headers={"content-type": "text/html"},
        )

    def short_budget(seconds):
        assert seconds == 10.0
        return move_on_after(0.1)

    _mock_title_http(monkeypatch, handler)
    monkeypatch.setattr("a13n_harness_ui.chat_titles.move_on_after", short_budget)
    calls = []
    model, recipe = _captured_title_model(calls)
    await generate_title(
        [
            TitleMessage(
                "user", "https://example.test/redirect https://example.test/slow https://u:p@example.test/secret"
            )
        ],
        model,
        recipe,
    )
    assert completed.is_set() and set(seen) == {"/slow", "/redirect", "/quick"}
    assert "https://example.test/redirect: Finished" in _prompt_text(calls[0][0])


def _png(size=(1200, 900), color="navy"):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _vision(policy=None):
    return HarnessModelCharacteristics(
        capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING}), image_input=policy
    )


@pytest.mark.anyio
async def test_images_resize_and_keep_first_four_or_earliest_and_latest():
    colors = [(index * 30, 0, 0) for index in range(6)]
    images = tuple(
        TitleAttachment(
            f"screenshot-{index}.png", "image/png", image=BinaryContent(_png(color=color), media_type="image/png")
        )
        for index, color in enumerate(colors)
    )
    cases = [
        ([TitleMessage("user", attachments=images)], colors[:4], 768, None),
        ([TitleMessage("user", attachments=(image,)) for image in images], [colors[0], *colors[-3:]], 768, None),
        (
            [TitleMessage("user", attachments=(image,)) for image in images],
            [colors[0], colors[-1]],
            320,
            ImageInputPolicy(max_image_dimension=320, max_image_bytes=32 * 1024, max_images=2),
        ),
    ]
    for messages, expected_colors, dimension_limit, policy in cases:
        calls = []
        model, recipe = _captured_title_model(calls, _vision(policy))
        await generate_title(messages, model, recipe)
        assert len(calls) == 1
        actual_colors = []
        for value in calls[0][0]:
            if isinstance(value, BinaryContent):
                with Image.open(BytesIO(value.data)) as image:
                    assert max(image.size) <= dimension_limit
                    assert len(value.data) <= (policy.max_image_bytes if policy else 256 * 1024)
                    actual_colors.append(image.convert("RGB").getpixel((0, 0)))
        # Thumbnail preparation may encode as JPEG.
        assert len(actual_colors) == len(expected_colors)
        for actual, expected in zip(actual_colors, expected_colors, strict=True):
            assert actual == pytest.approx(expected, abs=2)


@pytest.mark.anyio
async def test_title_keeps_image_name_when_thumbnail_preparation_drops_the_image():
    output = BytesIO()
    first = Image.new("RGB", (800, 500), "red")
    second = Image.new("RGB", (800, 500), "blue")
    first.save(output, format="GIF", save_all=True, append_images=[second], duration=50)
    image = TitleAttachment(
        "checkout-repro.gif", "image/gif", image=BinaryContent(output.getvalue(), media_type="image/gif")
    )
    calls = []
    model, recipe = _captured_title_model(calls, _vision())
    await generate_title([TitleMessage("user", attachments=(image,))], model, recipe)
    assert all(isinstance(value, str) for value in calls[0][0])
    assert "[Image: checkout-repro.gif]" in _prompt_text(calls[0][0])


@pytest.mark.anyio
async def test_image_filenames_fallback_without_model_vision_or_retained_bytes():
    reads = []

    async def read_attachment(key):
        reads.append(key)
        raise ValueError("Attachment is missing")

    messages = [
        TitleMessage(
            "user", "Diagnose screenshot", attachments=(TitleAttachment("image.png", "image/png", "generic"),)
        ),
        TitleMessage("user", attachments=(TitleAttachment("clipboard.png", "image/png", "clipboard"),)),
        TitleMessage("user", attachments=(TitleAttachment("build.png", "image/png", "named"),)),
        TitleMessage("user", attachments=(TitleAttachment("animation.gif", "image/gif", "gif"),)),
    ]
    for characteristics in (None, _vision(ImageInputPolicy(support_gif=False))):
        calls = []
        model, recipe = _captured_title_model(calls, characteristics)
        await generate_title(messages, model, recipe, read_attachment=read_attachment)
        assert all(isinstance(value, str) for value in calls[0][0])
        text = _prompt_text(calls[0][0])
        assert "User: Diagnose screenshot\n\nUser: [Image]" in text
        assert "User: [Image: build.png]" in text and "User: [Image: animation.gif]" in text
        assert reads == ([] if characteristics is None else ["generic", "clipboard", "named"])


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("kind", ["image", "file", "skill"])
async def test_non_text_first_submission_and_manual_title(tmp_path, monkeypatch, kind):
    root = _write_configuration(tmp_path)
    calls = []
    model, recipe = _captured_title_model(calls, _vision())
    if kind == "skill":
        prompt = prepare_skill_input("", catalog(), ("review",))
    else:
        upload = (
            AttachmentUpload("screenshot.png", _png(), "image/png")
            if kind == "image"
            else AttachmentUpload("record.txt", b"Private file bytes", "text/plain")
        )
        prompt = ComposerInput((ComposerAttachment(upload, "upload"),))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:

        async def utility_model(source, model_id, thread_id):
            return model, recipe

        monkeypatch.setattr(app, "_utility_model", utility_model)
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt)
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        with fail_after(5):
            while (await app.get_thread(thread_id)).thread.title != "Generated title":
                await sleep(0.02)
        assert await app.suggest_chat_title(thread_id) == "Generated title"
    assert len(calls) == 2
    for content, _ in calls:
        if kind == "image":
            assert sum(isinstance(value, BinaryContent) for value in content) == 1
            assert (
                "Attachment " not in _prompt_text(content)
                and _prompt_text(content).count("[Image: screenshot.png]") == 1
            )
        elif kind == "file":
            text = _prompt_text(content)
            assert text.count("[File: record.txt]") == 1 and "Private file bytes" not in text
            assert "thread-files" not in text
        else:
            assert "<skills>\n- review: Selected workflow\n</skills>" in _prompt_text(content)


def _annotated_request(text, metadata):
    request = ModelRequest(parts=[UserPromptPart(text)])
    return annotate_prompt(request, 0, [ContentItem(text, ContentMetadata.from_native(metadata))])


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
async def test_manual_title_samples_history_edges_and_only_conversation_parts(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    calls = []
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Seed continuation")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        thread = await app._store.threads.get(thread_id)
        saved = await read_continuation(app._store.objects, thread.continuation, include_display=True)
        long_id = (
            await app.stage_thread_attachment(
                thread_id=thread_id, upload=AttachmentUpload("input-text.txt", b"Retained user intent", "text/plain")
            )
        ).attachment_id
        history = (
            ModelRequest(parts=[UserPromptPart("First user goal")]),
            ModelResponse(parts=[TextPart("First useful reply")]),
            *(ModelRequest(parts=[UserPromptPart(f"Middle position {i}")]) for i in range(28)),
            _annotated_request(
                "replace with file contents",
                {
                    "harness_ui": {
                        "long_text": True,
                        "attachment": {
                            "name": "input-text.txt",
                            "attachment_id": long_id,
                            "media_type": "text/plain",
                        },
                    }
                },
            ),
            _annotated_request("non-user steering secret", {"a13n.steering-source": "system"}),
            _annotated_request("MCP context secret", {"harness_ui": {"mcp_app_context": True}}),
            _annotated_request("long text skip notice", {"harness_ui": {"long_text_skipped": True}}),
            ModelRequest(parts=[SystemPromptPart("system prompt secret")]),
            ModelResponse(parts=[TextPart("compaction secret")], metadata={"keep": "compact"}),
            ModelResponse(
                parts=[ThinkingPart("isolated thought secret"), ToolCallPart("view", {"file": "tool secret"})]
            ),
            ModelRequest(parts=[ToolReturnPart("view", "tool result secret")]),
            ModelResponse(
                parts=[ThinkingPart("mixed thought secret"), TextPart("reply-start " + "word " * 1000 + "reply-end")]
            ),
            ModelResponse(parts=[TextPart("   \n")]),
        )
        value = saved.model_copy(
            update={
                "display": None,
                "harness_state": HarnessState.new(thread_id=thread_id, message_history=history),
            }
        )
        replacement = await publish_continuation(app._store.objects, value)
        await app._store.threads.select_continuation(
            thread_id=thread_id,
            expected=thread.continuation,
            replacement=replacement,
            read_model=project_continuation(value),
        )
        assert await app._store.publish_work(thread_id, replacement, value.harness_state)

        async def utility_model(source, model_id, thread_id):
            return _captured_title_model(calls)

        monkeypatch.setattr(app, "_utility_model", utility_model)
        entries = await app._projections.title_entries(thread_id)
        assert [entry.position for entry in entries] == [0, 1, *range(8, 31), 38]
        assert await app.suggest_chat_title(thread_id) == "Generated title"
        text = _prompt_text(calls[0][0])
        assert text.startswith("User: First user goal\n\nAssistant: First useful reply\n\n")
        assert "User: Retained user intent" in text and "input-text.txt" not in text
        assert "secret" not in text and "long text skip notice" not in text
        final = text.rsplit("Assistant: ", 1)[1]
        assert final.startswith("reply-start ") and final.endswith(" reply-end")
        assert len(final.split()) <= 200
