import base64
import json

import anyio
import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.errors import ModelResolutionError
from a13n_harness.models.codex_dictation import CodexDictationError
from a13n_harness_ui import dictation
from a13n_harness_ui.dictation import DictationDraft, DictationInput, clean_transcript, transcribe_audio
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage
from pydantic_ai import BinaryContent, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel

from .test_subscription_routing import _accounts


def recording(**kwargs):
    audio = b"RIFF" + bytes(4) + b"WAVE" + bytes(40)
    return DictationInput(audio=base64.b64encode(audio).decode(), **kwargs)


@pytest.mark.anyio
async def test_audio_is_sent_as_audio_with_reference_context_and_no_tools():
    async def reply(messages, info):
        prompt = next(
            part
            for m in messages
            if isinstance(m, ModelRequest)
            for part in m.parts
            if isinstance(part, UserPromptPart)
        )
        context, audio = prompt.content
        assert "Pyright" in context and "类型检查" in context
        assert json.loads(context)["draft"]["selected"] == "Mypy"
        assert isinstance(audio, BinaryContent) and audio.media_type == "audio/wav"
        assert "language from the audio" in info.instructions
        assert not info.function_tools
        return ModelResponse(parts=[TextPart("Please run Pyright.")])

    request = recording(title="类型检查", context="Human: Pyright", draft=DictationDraft(selected="Mypy"))
    assert await transcribe_audio(request, FunctionModel(reply), {}) == "Please run Pyright."


@pytest.mark.anyio
async def test_cleanup_is_text_only_with_frozen_draft_and_no_tools_or_history():
    async def reply(messages, info):
        prompts = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]
        assert len(prompts) == 1 and isinstance(prompts[0].content, str)
        payload = json.loads(prompts[0].content)
        assert payload == {
            "transcript": "Um run pie right",
            "chat_title": "Types",
            "nearby_messages": "Pyright",
            "draft": {"before": "Please ", "selected": "Mypy", "after": " now."},
        }
        assert not info.function_tools
        assert info.model_settings["openai_reasoning_effort"] == "low"
        assert info.model_settings["openai_store"] is False
        assert "Do not answer questions" in info.instructions
        return ModelResponse(parts=[TextPart("Run Pyright")])

    request = recording(
        title="Types", context="Pyright", draft=DictationDraft(before="Please ", selected="Mypy", after=" now.")
    )
    result = await clean_transcript(request, " Um run pie right ", FunctionModel(reply))
    assert result.text == "Run Pyright" and result.warning is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["empty", "error"])
async def test_cleanup_failure_preserves_raw_transcript(failure):
    async def reply(messages, info):
        if failure == "error":
            raise RuntimeError("provider failed")
        return ModelResponse(parts=[TextPart(" ")])

    result = await clean_transcript(recording(), " raw speech ", FunctionModel(reply))
    assert result.text == "raw speech" and result.warning == "cleanup_failed"


@pytest.mark.anyio
async def test_empty_transcript_skips_cleanup():
    async def reply(messages, info):
        pytest.fail("Empty ASR must not invoke a model")

    assert (await clean_transcript(recording(), " \n", FunctionModel(reply))).text == ""


@pytest.mark.anyio
async def test_cleanup_cancellation_discards_result():
    started = anyio.Event()

    async def reply(messages, info):
        started.set()
        await anyio.sleep_forever()

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as tasks:

            async def run():
                await clean_transcript(recording(), "raw", FunctionModel(reply))
                pytest.fail("Cancellation must propagate")

            tasks.start_soon(run)
            await started.wait()
            scope.cancel()


@pytest.fixture
async def subscription(tmp_path, monkeypatch):
    catalog, accounts, group = await _accounts(tmp_path)
    usage = SubscriptionUsage(catalog)
    configuration = RunConfiguration()
    state = {"raw": "raw speech", "asr_errors": {}, "cleanup_errors": {}, "disable_after_asr": False}
    calls = {"asr": [], "cleanup": []}
    clients = []
    create_client = dictation.create_model_http_client

    def client(**kwargs):
        assert kwargs == {"retry": None, "timeout": 30, "configuration": configuration}
        value = create_client(**kwargs)
        clients.append(value)
        return value

    async def asr(audio, credential_source, **kwargs):
        assert audio[:4] == b"RIFF" and kwargs["configuration"] is configuration
        account_id = (await credential_source.load()).account_id
        calls["asr"].append(account_id)
        await kwargs["response_headers"](
            {
                "x-codex-primary-used-percent": "12",
                "x-codex-primary-window-minutes": "300",
                "x-codex-primary-reset-at": "2000000000",
            }
        )
        if error := state["asr_errors"].get(account_id):
            raise error
        if state["disable_after_asr"]:
            await catalog.update(account_id, enabled=False)
        return state["raw"]

    def model(name, *, credential_source, http_client, response_headers):
        assert name == "preset-model" and http_client in clients

        async def reply(messages, info):
            account_id = (await credential_source.load()).account_id
            calls["cleanup"].append(account_id)
            await response_headers(
                {
                    "x-codex-primary-used-percent": "24",
                    "x-codex-primary-window-minutes": "300",
                    "x-codex-primary-reset-at": "2000000000",
                }
            )
            if error := state["cleanup_errors"].get(account_id):
                raise error
            return ModelResponse(parts=[TextPart("clean speech")])

        return FunctionModel(reply, model_name=name)

    monkeypatch.setattr("a13n_harness.models.codex_dictation.transcribe", asr)
    monkeypatch.setattr(dictation, "create_model_http_client", client)
    monkeypatch.setattr("a13n_harness.models.codex.CodexRequestModel", model)

    async def run(selected_group=group):
        return await dictation.transcribe_subscription(
            recording(), selected_group, catalog, usage, "preset-model", configuration=configuration
        )

    yield run, state, calls, accounts, group, usage, clients
    assert all(client.is_closed for client in clients)
    assert len({id(client) for client in clients}) == len(clients)


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["", "raw speech"])
async def test_subscription_skips_empty_cleanup(subscription, raw):
    run, state, calls, accounts, _, _, clients = subscription
    state["raw"] = raw
    result = await run()
    assert result.text == ("clean speech" if raw else "") and result.warning is None
    assert calls == {"asr": [accounts[0].id], "cleanup": [accounts[0].id] if raw else []}
    assert bool(clients) == bool(raw)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 429])
async def test_asr_rejects_and_switches_accounts_only_on_definite_rejection(subscription, status):
    run, state, calls, accounts, _, usage, _ = subscription
    state["asr_errors"][accounts[0].id] = CodexDictationError("rejected", status_code=status)
    result = await run()
    assert result.text == "clean speech"
    assert calls == {"asr": [account.id for account in accounts], "cleanup": [accounts[1].id]}
    assert "preset-model" in (await usage.read(accounts[0])).rejected
    assert (await usage.read(accounts[1])).usage.rate_limit.primary_window.used_percent == 24


@pytest.mark.anyio
@pytest.mark.parametrize("status", [None, 403, 500])
async def test_ambiguous_or_non_account_asr_failure_is_not_replayed(subscription, status):
    run, state, calls, accounts, _, usage, _ = subscription
    state["asr_errors"][accounts[0].id] = CodexDictationError("failed", status_code=status)
    with pytest.raises(HarnessUiError) as error:
        await run()
    assert error.value.code == "dictation_provider_error"
    assert calls == {"asr": [accounts[0].id], "cleanup": []}
    assert not (await usage.read(accounts[0])).rejected


@pytest.mark.anyio
async def test_asr_exhaustion_attempts_each_account_once(subscription):
    run, state, calls, accounts, _, _, _ = subscription
    state["asr_errors"] = {account.id: CodexDictationError("limited", status_code=429) for account in accounts}
    with pytest.raises(ModelResolutionError) as error:
        await run()
    assert error.value.code == "subscription_group_unavailable"
    assert calls == {"asr": [account.id for account in accounts], "cleanup": []}


@pytest.mark.anyio
async def test_cleanup_selects_independently_after_asr(subscription):
    run, state, calls, accounts, _, _, _ = subscription
    state["disable_after_asr"] = True
    assert (await run()).text == "clean speech"
    assert calls == {"asr": [accounts[0].id], "cleanup": [accounts[1].id]}


@pytest.mark.anyio
@pytest.mark.parametrize("all_rejected", [False, True])
async def test_cleanup_uses_normal_group_failover_without_repeating_asr(subscription, all_rejected):
    run, state, calls, accounts, _, _, _ = subscription
    state["cleanup_errors"] = {
        account.id: ModelHTTPError(429, "preset-model", {"error": {"code": "usage_limit_reached"}})
        for account in (accounts if all_rejected else accounts[:1])
    }
    result = await run()
    assert result.text == ("raw speech" if all_rejected else "clean speech")
    assert result.warning == ("cleanup_failed" if all_rejected else None)
    assert calls == {"asr": [accounts[0].id], "cleanup": [account.id for account in accounts]}


@pytest.mark.anyio
async def test_pinned_account_never_uses_other_accounts(subscription):
    run, state, calls, accounts, group, _, _ = subscription
    state["asr_errors"][accounts[0].id] = CodexDictationError("rejected", status_code=401)
    with pytest.raises(ModelResolutionError):
        await run(group.model_copy(update={"accounts": group.accounts[:1]}))
    assert calls == {"asr": [accounts[0].id], "cleanup": []}
