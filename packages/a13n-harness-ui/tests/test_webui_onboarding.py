"""First-use discovery and creation stay local, explicit, and recoverable."""

from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.setup import SetupSelection
from a13n_harness_ui.errors import HarnessUiError, StoreIntegrityError
from a13n_harness_ui.model_authoring import ModelChoices, ModelOptionsRequest, model_options
from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS, API_PROVIDERS, settings_presets
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from anyio import create_task_group


def test_model_choices_project_release_owned_connections() -> None:
    choices = ModelChoices()
    subscriptions = [c for c in choices.connections if c.authentication != "api_key"]
    assert [c.id for c in subscriptions] == [
        "codex",
        "grok-subscription",
        "copilot-subscription",
        "chatgpt-subscription",
    ]
    providers = [c for c in choices.connections if c.authentication == "api_key"]
    assert tuple(provider.id for provider in providers) == tuple(p.route for p in API_PROVIDERS)
    for provider in providers:
        assert tuple(m.value for m in provider.models) == API_MODEL_SUGGESTIONS[provider.id]


def test_api_model_options_reuse_presets_without_provider_discovery() -> None:
    options = model_options(
        ModelOptionsRequest(connection="openai-responses", model_id="gpt-5.4", base_url="https://api.openai.com/v1")
    )
    assert [p.settings for p in options.presets] == [
        p.settings for p in settings_presets("openai-responses", "gpt-5.4")
    ]
    assert options.context_window is not None and options.context_window <= 350000
    custom = model_options(
        ModelOptionsRequest(connection="openai-chat", model_id="custom-deployment", base_url="http://localhost:8080/v1")
    )
    assert custom.known_context_window is None
    assert custom.context_window == 350000
    with pytest.raises(HarnessUiError, match="without credentials"):
        model_options(
            ModelOptionsRequest(connection="openai-chat", model_id="custom", base_url="https://key:secret@example.com")
        )
    with pytest.raises(HarnessUiError, match="native endpoint"):
        model_options(ModelOptionsRequest(connection="xai", model_id="grok-4.6", base_url="https://example.com"))


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
@pytest.mark.parametrize("separator", ["_", "-"])
async def test_first_conversation_identity_is_create_only_and_survives_restart(tmp_path: Path, separator: str) -> None:
    path = tmp_path / "configuration" / "config.yaml"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "state"), pricing_auto_update=False)
    identity = f"thread{separator}" + "a" * 32
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("connection", "model_id", "authentication", "model_file"),
    [
        ("codex", "gpt-6.2-sol", "codex_subscription", "codex"),
        ("copilot-subscription", "synthetic-chat-model", "copilot_subscription", "copilot"),
    ],
)
async def test_shared_model_authoring_http_contract_is_inert_and_has_one_setup_shape(
    tmp_path: Path, connection: str, model_id: str, authentication: str, model_file: str
) -> None:
    import httpx
    from a13n_harness_ui.webui import create_webui

    configuration = tmp_path / "config.yaml"
    server = create_webui(
        lambda: open_harness_ui_app(
            HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
            configuration_path=configuration,
        ),
        api_key="model-authoring-test",
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://localhost",
            headers={"Authorization": "Bearer model-authoring-test"},
        ) as client,
    ):
        choices = await client.get("/api/models/choices")
        assert choices.status_code == 200
        assert choices.json() == ModelChoices().model_dump(mode="json")
        if connection == "codex":
            codex = next(choice for choice in choices.json()["connections"] if choice["id"] == "codex")
            assert codex["default_model"] == model_id
        catalog = await client.get("/api/models/catalog")
        assert catalog.status_code == 200 and catalog.json()["items"]
        request = {"connection": connection, "model_id": model_id}
        options = await client.post("/api/models/options", json=request)
        assert options.status_code == 200
        assert options.json()["supports_service_tier"] is (connection == "codex")
        recipe = await client.post("/api/models/prepare", json=request)
        assert recipe.status_code == 200
        assert recipe.json()["authentication"] == {"kind": authentication}
        assert not configuration.exists()
        assert not (configuration.parent / "models").exists()
        assert "/api/setup/model-options" not in server.openapi()["paths"]
        assert (await client.post("/api/setup/model-options", json={})).status_code == 405
        rejected = await client.post(
            "/api/setup/preview", json={"providers": ["codex"], "environment_profile": "environment-native"}
        )
        assert rejected.status_code == 400
        preview = await client.post(
            "/api/setup/preview", json={"model": recipe.json(), "environment_profile": "environment-native"}
        )
        assert preview.status_code == 200, preview.text
        assert f"models/{model_file}.yaml" in preview.json()["files"]
        assert not configuration.exists()
        published = await client.post(
            "/api/setup/apply",
            json={"selection": {"model": recipe.json(), "environment_profile": "environment-native"}},
        )
        assert published.status_code == 200, published.text
        assert published.json()["completed"]
        assert configuration.exists()
        assert (configuration.parent / "models" / f"{model_file}.yaml").is_file()


@pytest.mark.anyio
async def test_setup_status_exposes_acp_agents_and_environment_names_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-value-that-must-stay-on-host")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  \t  ")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert (await app.setup_status()).fresh
    agents = tmp_path / "acp_agents"
    agents.mkdir()
    (agents / "cli.yaml").write_text(
        'schema_version: "1"\nkind: acp_agent\nid: acp-local\nname: Local peer\npreset: claude_code\n'
    )
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        status = await app.setup_status()
        assert not status.fresh
        assert "OPENAI_API_KEY" in status.detected_env_keys
        assert "ANTHROPIC_API_KEY" not in status.detected_env_keys
        assert "fixture-value-that-must-stay-on-host" not in status.model_dump_json()
    path.write_text('schema_version: "1"\ndefaults: {agent: acp-local}\n')
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        status = await app.setup_status()
        assert status.agents["acp-local"] == "Local peer"
        assert status.acp_presets["acp-local"] == "claude_code"
        assert status.default_agent == "acp-local"
        assert not status.needed


@pytest.mark.anyio
@pytest.mark.parametrize("project_override", [False, True])
async def test_setting_acp_default_rejects_an_inherited_sandbox(tmp_path: Path, project_override: bool) -> None:
    path = tmp_path / "config.yaml"
    data_root = tmp_path / "data"
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=data_root)), configuration_path=path
    ) as app:
        initial = SetupSelection(
            project="project-local", project_path=str(tmp_path), environment_profile="environment-native"
        )
        assert (await app.apply_setup(initial)).completed
    if project_override:
        project = tmp_path / "projects" / "project-local.yaml"
        with project.open("a") as stream:
            stream.write("defaults: {environment_profile: environment-sandbox}\n")
    else:
        path.write_text(path.read_text().replace("environment-native", "environment-sandbox"))
    root = path.read_bytes()
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=data_root)), configuration_path=path
    ) as app:
        selection = SetupSelection.model_validate(
            {
                "primary_acp_agent": {"preset": "claude_code"},
                "configure_workspace": False,
                "environment_profile": "environment-native",
            }
        )
        for operation in (app.preview_setup, app.apply_setup):
            with pytest.raises(HarnessUiError) as error:
                await operation(selection)
            assert error.value.code == "acp_requires_native_environment"
        assert path.read_bytes() == root
        assert not (tmp_path / "acp_agents").exists()
        assert (await app.apply_setup(selection.model_copy(update={"set_as_default": False}))).completed
        assert path.read_bytes() == root
        # The CLI's named Add Agent operation does not replace the default either.
        assert (await app.apply_setup(selection.model_copy(update={"new_agent_id": "acp-named"}))).completed
        assert path.read_bytes() == root


@pytest.mark.anyio
async def test_preset_auth_http_probes_and_handles_login_without_saving_configuration(tmp_path: Path) -> None:
    import sys

    import httpx
    from a13n_harness_ui.webui import create_webui
    from anyio import fail_after, sleep

    configuration = tmp_path / "config.yaml"
    peer = Path(__file__).resolve().parents[2] / "a13n-harness/tests/fixtures/acp_peer.py"
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    server = create_webui(
        lambda: open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui"),
        api_key="owner-fixture",
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client,
    ):
        endpoint = "/api/acp-agents/preset-auth"
        candidate = {"preset": "custom", "command": sys.executable, "args": [str(peer), "auth-required"]}
        probe = {**candidate, "action": "probe"}
        unauthorized = await client.post(endpoint, json=probe)
        assert unauthorized.status_code in (401, 403)
        client.headers["Authorization"] = "Bearer owner-fixture"
        response = await client.post(endpoint, json=probe)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "auth_required"
        invalid = await client.post(endpoint, json={**candidate, "action": "login", "method_id": "unknown"})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "acp_auth_method_invalid"
        results = []

        async def login() -> None:
            result = await client.post(endpoint, json={**candidate, "action": "login", "method_id": "browser"})
            assert result.status_code == 200, result.text
            results.append(result)

        with fail_after(15):
            async with create_task_group() as group:
                group.start_soon(login)
                while not (requests := (await client.get(f"{endpoint}/inputs")).json()):
                    await sleep(0.02)
                (request,) = requests
                assert request["thread_id"] == "acp-setup-preview"
                assert request["url"] == "https://example.com/sign-in"
                response = await client.post(
                    f"{endpoint}/inputs/{request['request_id']}/response", json={"action": "accept"}
                )
                assert response.status_code == 200, response.text
            assert results[0].status_code == 200, results[0].text
            assert results[0].json()["state"] == "login_completed"
    assert not configuration.exists()
    assert not (tmp_path / "acp_agents").exists()
