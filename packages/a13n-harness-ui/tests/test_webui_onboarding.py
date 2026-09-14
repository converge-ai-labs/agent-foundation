"""First-use discovery and creation stay local, explicit, and recoverable."""

from pathlib import Path
from typing import get_args

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.setup import SetupSelection
from a13n_harness_ui.errors import HarnessUiError, StoreIntegrityError
from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS, API_PROVIDERS, settings_presets
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.setup import SetupModelOptionsRequest, setup_choices, setup_model_options
from anyio import create_task_group


def test_setup_choices_project_existing_release_catalog() -> None:
    choices = setup_choices()
    assert choices.defaults == SetupSelection(environment_profile="environment-native")
    for provider in ("codex", "grok"):
        assert tuple(choice.value for choice in choices.subscription_models[provider]) == get_args(
            SetupSelection.model_fields[f"{provider}_model"].annotation
        )
    assert tuple(provider.value for provider in choices.api_providers) == tuple(p.route for p in API_PROVIDERS)
    for provider in choices.api_providers:
        assert provider.models == API_MODEL_SUGGESTIONS[provider.value]


def test_api_model_options_reuse_presets_without_provider_discovery() -> None:
    options = setup_model_options(
        SetupModelOptionsRequest(provider="openai-responses", model_id="gpt-5.4", base_url="https://api.openai.com/v1")
    )
    assert [p.settings for p in options.presets] == [
        p.settings for p in settings_presets("openai-responses", "gpt-5.4")
    ]
    assert options.context_window <= 350000
    custom = setup_model_options(
        SetupModelOptionsRequest(
            provider="openai-chat", model_id="custom-deployment", base_url="http://localhost:8080/v1"
        )
    )
    assert custom.known_context_window is None
    assert custom.context_window == 350000
    with pytest.raises(HarnessUiError, match="without credentials"):
        setup_model_options(
            SetupModelOptionsRequest(
                provider="openai-chat", model_id="custom", base_url="https://key:secret@example.com"
            )
        )
    with pytest.raises(HarnessUiError, match="default endpoint"):
        setup_model_options(
            SetupModelOptionsRequest(provider="xai", model_id="grok-4.6", base_url="https://example.com")
        )


@pytest.mark.anyio
async def test_first_use_scope_is_stable_but_partial_sources_are_not_fresh(tmp_path: Path) -> None:
    path = tmp_path / "configuration" / "config.yaml"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        initial = await app.setup_status()
        assert initial.fresh and initial.needed
        assert not path.exists()
        scope = initial.draft_scope
        assert len(scope) == 24
        assert await app.active_login() is None
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert (await app.setup_status()).draft_scope == scope
        agents = path.parent / "agents"
        agents.mkdir(parents=True)
        (agents / "partial.yaml").write_text("kind: agent\n", encoding="utf-8")
        assert not (await app.setup_status()).fresh
    other = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "other"), pricing_auto_update=False)
    async with open_harness_ui_app(other, configuration_path=path) as app:
        assert (await app.setup_status()).draft_scope != scope


@pytest.mark.anyio
async def test_first_conversation_identity_is_create_only_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "configuration" / "config.yaml"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    identity = "thread-" + "a" * 32
    outcomes = []
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        selection = SetupSelection(environment_profile="environment-native")
        assert (await app.apply_setup(selection)).completed
        status = await app.setup_status()
        assert not status.fresh and not status.needed

        async def create() -> None:
            try:
                outcomes.append(await app.create_thread(thread_id=identity))
            except StoreIntegrityError as exc:
                outcomes.append(exc)

        async with create_task_group() as tasks:
            tasks.start_soon(create)
            tasks.start_soon(create)
        conflicts = [outcome for outcome in outcomes if isinstance(outcome, StoreIntegrityError)]
        assert len(conflicts) == 1
        assert conflicts[0].code == "thread_exists"
        thread = await app.get_thread(identity)
        assert thread.thread.thread_id == identity
        assert thread.thread.root_activity.state == "inactive"
        assert (await app.list_threads()).total == 1
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert (await app.get_thread(identity)).thread.thread_id == identity
        with pytest.raises(StoreIntegrityError, match="already exists"):
            await app.create_thread(thread_id=identity, title="Different request")
        assert (await app.list_threads()).total == 1
