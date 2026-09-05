from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from a13n_ui.cli import main
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.content_plugins import ContentPluginStore
from a13n_ui.errors import ContentPluginError


@pytest.mark.anyio
async def test_install_list_and_uninstall_deletes_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    installed = await store.install(os.fspath(repository))

    assert installed.plugin_id == "plugin-reviewer"
    assert installed.version == "1.0.0"
    assert len(installed.commit) == 40
    assert Path(installed.path).is_dir()
    assert Path(installed.skills_path or "").is_dir()
    assert tuple(Path(path).name for path in installed.subagent_paths) == ("explorer.md",)
    assert await store.list() == (installed,)

    removed = await store.uninstall("plugin-reviewer")

    assert removed.plugin_id == installed.plugin_id
    assert removed.removed_path == installed.path
    assert await store.list() == ()
    assert not Path(installed.path).exists()
    assert tuple(store.root.iterdir()) == ()


@pytest.mark.anyio
async def test_install_requires_selection_for_multi_plugin_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer", "plugin-writer"))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    with pytest.raises(ContentPluginError) as required:
        await store.install(os.fspath(repository))

    assert required.value.code == "content_plugin_selection_required"
    assert required.value.details["plugin_ids"] == ["plugin-reviewer", "plugin-writer"]

    installed = await store.install(os.fspath(repository), plugin_id="plugin-writer")

    assert installed.plugin_id == "plugin-writer"


@pytest.mark.anyio
async def test_install_rejects_duplicate_registration(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    await store.install(os.fspath(repository))

    with pytest.raises(ContentPluginError) as duplicate:
        await store.install(os.fspath(repository))

    assert duplicate.value.code == "content_plugin_already_installed"


@pytest.mark.anyio
async def test_install_rejects_symlinked_plugin_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",), commit=False)
    target = repository / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (repository / "plugins" / "reviewer" / "skills" / "review" / "linked.txt").symlink_to(target)
    _commit(repository)
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    with pytest.raises(ContentPluginError) as invalid:
        await store.install(os.fspath(repository))

    assert invalid.value.code == "content_plugin_invalid"
    assert await store.list() == ()


def test_cli_installs_lists_and_uninstalls_content_plugin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    data_root = tmp_path / "data"

    main(["--data-root", os.fspath(data_root), "plugin", "install", os.fspath(repository), "--format", "json"])
    installed = json.loads(capsys.readouterr().out)["installed"]
    assert installed["plugin_id"] == "plugin-reviewer"
    assert Path(installed["path"]).is_dir()

    main(["--data-root", os.fspath(data_root), "plugin", "list", "--format", "json"])
    listed = json.loads(capsys.readouterr().out)["plugins"]
    assert [(item["plugin_id"], item["path"]) for item in listed] == [("plugin-reviewer", installed["path"])]

    main(["--data-root", os.fspath(data_root), "plugin", "uninstall", "plugin-reviewer", "--format", "json"])
    removed = json.loads(capsys.readouterr().out)
    assert removed == {"plugin_id": "plugin-reviewer", "removed_path": installed["path"]}
    assert not Path(installed["path"]).exists()


@pytest.mark.anyio
async def test_configuration_loads_plugin_subagent_and_prefers_local_override(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    await store.install(os.fspath(repository))
    configuration = _configuration(tmp_path / "configuration")

    loaded = await load_agent_ui_configuration(configuration, content_plugin_root=store.root)

    assert tuple(item.plugin_id for item in loaded.content_plugins) == ("plugin-reviewer",)
    assert loaded.subagents["subagent-explorer"].description == "Explore a repository."
    assert any(
        item.relative_path == "content-plugins/plugin-reviewer/subagents/explorer.md"
        and item.resource_id == "subagent-explorer"
        for item in loaded.sources
    )

    local = configuration.parent / "subagents" / "explorer.md"
    local.parent.mkdir()
    local.write_text(
        "---\nname: explorer\ndescription: Local explorer.\n---\n\nUse local instructions.\n",
        encoding="utf-8",
    )
    overridden = await load_agent_ui_configuration(configuration, content_plugin_root=store.root)

    assert overridden.subagents["subagent-explorer"].description == "Local explorer."
    plugin_source = next(
        item
        for item in overridden.sources
        if item.relative_path == "content-plugins/plugin-reviewer/subagents/explorer.md"
    )
    assert plugin_source.resource_kind == "content_plugin_subagent"
    assert plugin_source.resource_id is None


@pytest.mark.anyio
async def test_configuration_resolves_plugin_subagent_conflicts_deterministically(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer", "plugin-writer"))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    await store.install(os.fspath(repository), plugin_id="plugin-reviewer")
    await store.install(os.fspath(repository), plugin_id="plugin-writer")
    configuration = _configuration(tmp_path / "configuration")

    loaded = await load_agent_ui_configuration(configuration, content_plugin_root=store.root)
    assert "subagent-explorer" in loaded.subagents
    assert any("Overrides plugin subagent" in item for item in loaded.content_plugin_diagnostics)


@pytest.mark.anyio
async def test_installed_content_can_be_edited_and_deleted_on_uninstall(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    installed = await store.install(os.fspath(repository))
    fingerprint_before = await store.fingerprint()
    plugin_root = Path(installed.path)
    (plugin_root / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review an edited change.\n---\n\nReview the local version.\n",
        encoding="utf-8",
    )
    (plugin_root / "subagents" / "explorer.md").write_text(
        "---\nname: explorer\ndescription: Explore the edited repository.\n---\n\nInspect local changes.\n",
        encoding="utf-8",
    )

    fingerprint_after = await store.fingerprint()
    listed = await store.list()
    loaded = await load_agent_ui_configuration(
        _configuration(tmp_path / "configuration"),
        content_plugin_root=store.root,
    )
    removed = await store.uninstall("plugin-reviewer")

    assert fingerprint_after != fingerprint_before
    assert listed[0].commit == installed.commit
    assert loaded.subagents["subagent-explorer"].description == "Explore the edited repository."
    assert loaded.subagents["subagent-explorer"].body == "Inspect local changes."
    assert removed.removed_path == installed.path
    assert await store.list() == ()


@pytest.mark.anyio
async def test_reinstall_starts_with_fresh_repository_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    installed = await store.install(os.fspath(repository))
    skill_document = Path(installed.path) / "skills" / "review" / "SKILL.md"
    edited = "---\nname: review\ndescription: Locally edited.\n---\n\nKeep this edit.\n"
    skill_document.write_text(edited, encoding="utf-8")
    await store.uninstall("plugin-reviewer")

    reinstalled = await store.install(os.fspath(repository))

    assert reinstalled.path == installed.path
    assert skill_document.read_text(encoding="utf-8") != edited


@pytest.mark.anyio
async def test_invalid_local_subagent_does_not_block_plugin_or_configuration(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")
    installed = await store.install(os.fspath(repository))
    (Path(installed.path) / "subagents" / "explorer.md").write_text(
        "Missing frontmatter.\n",
        encoding="utf-8",
    )

    assert len(await store.list()) == 1
    loaded = await load_agent_ui_configuration(_configuration(tmp_path / "config"), content_plugin_root=store.root)
    assert loaded.subagents == {}
    assert loaded.content_plugin_diagnostics
    assert loaded.content_plugins[0].skills_path is not None


@pytest.mark.anyio
async def test_install_keeps_invalid_subagent_for_editing(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",), commit=False)
    (repository / "plugins" / "reviewer" / "subagents" / "explorer.md").write_text(
        "Missing frontmatter.\n",
        encoding="utf-8",
    )
    _commit(repository)
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    installed = await store.install(os.fspath(repository))
    assert Path(installed.path).is_dir()
    loaded = await load_agent_ui_configuration(_configuration(tmp_path / "config"), content_plugin_root=store.root)
    assert loaded.content_plugin_diagnostics


@pytest.mark.anyio
async def test_install_loads_duplicate_subagent_ids_deterministically(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",), commit=False)
    (repository / "plugins" / "reviewer" / "subagents" / "second.md").write_text(
        "---\nname: explorer\ndescription: Duplicate explorer.\n---\n\nDuplicate.\n",
        encoding="utf-8",
    )
    _commit(repository)
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    installed = await store.install(os.fspath(repository))
    assert Path(installed.path).is_dir()
    loaded = await load_agent_ui_configuration(_configuration(tmp_path / "config"), content_plugin_root=store.root)
    assert loaded.content_plugin_diagnostics


@pytest.mark.anyio
async def test_failed_directory_publication_leaves_no_installed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr(Path, "rename", fail_link)
    with pytest.raises(ContentPluginError) as unavailable:
        await store.install(os.fspath(repository))

    assert unavailable.value.code == "content_plugin_install_failed"
    assert tuple(store.root.iterdir()) == ()


@pytest.mark.anyio
async def test_uninstall_rejects_symlinked_store_root_without_deleting_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    installed = outside / "installed"
    installed.mkdir(parents=True)
    target = installed / "plugin-reviewer.json"
    target.write_text("not a registration", encoding="utf-8")
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "content-plugins").symlink_to(outside, target_is_directory=True)
    store = ContentPluginStore(data_root / "content-plugins")

    with pytest.raises(ContentPluginError) as invalid:
        await store.uninstall("plugin-reviewer")

    assert invalid.value.code == "content_plugin_store_invalid"
    assert target.read_text(encoding="utf-8") == "not a registration"


@pytest.mark.anyio
async def test_uninstall_rejects_unknown_plugin(tmp_path: Path) -> None:
    store = ContentPluginStore(tmp_path / "data" / "content-plugins")

    with pytest.raises(ContentPluginError) as missing:
        await store.uninstall("plugin-missing")

    assert missing.value.code == "content_plugin_not_installed"


def _configuration(path: Path) -> Path:
    path.mkdir(parents=True)
    configuration = path / "a13n-ui.yaml"
    configuration.write_text('schema_version: "2"\n', encoding="utf-8")
    models = path / "models"
    models.mkdir()
    (models / "primary.yaml").write_text(
        "\n".join(
            (
                'schema_version: "1"',
                "kind: model",
                "id: model-primary",
                "name: Primary",
                "route: openai:gpt-5",
                "authentication: {kind: api_key, env: OPENAI_API_KEY}",
                "",
            )
        ),
        encoding="utf-8",
    )
    agents = path / "agents"
    agents.mkdir()
    (agents / "assistant.yaml").write_text(
        "\n".join(
            (
                'schema_version: "1"',
                "kind: agent",
                "id: agent-assistant",
                "name: Assistant",
                "model: model-primary",
                "subagents: [{markdown: subagent-explorer}]",
                "",
            )
        ),
        encoding="utf-8",
    )
    return configuration


def _repository(path: Path, plugin_ids: tuple[str, ...], *, commit: bool = True) -> Path:
    path.mkdir(parents=True)
    marketplace_entries: list[str] = []
    for plugin_id in plugin_ids:
        short_name = plugin_id.removeprefix("plugin-")
        plugin_root = path / "plugins" / short_name
        (plugin_root / ".a13n-plugin").mkdir(parents=True)
        (plugin_root / "skills" / "review").mkdir(parents=True)
        (plugin_root / "subagents").mkdir(parents=True)
        (plugin_root / ".a13n-plugin" / "plugin.yaml").write_text(
            "\n".join(
                (
                    'schema_version: "1"',
                    "kind: content_plugin",
                    f"id: {plugin_id}",
                    f"name: {short_name.title()} Toolkit",
                    'version: "1.0.0"',
                    f"description: Content for {short_name}.",
                    "skills: ./skills",
                    "subagents: ./subagents",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (plugin_root / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review a change.\n---\n\nReview carefully.\n",
            encoding="utf-8",
        )
        (plugin_root / "subagents" / "explorer.md").write_text(
            "---\nname: explorer\ndescription: Explore a repository.\n---\n\nInspect the repository.\n",
            encoding="utf-8",
        )
        marketplace_entries.append(f"  - path: ./plugins/{short_name}")
    marketplace = path / ".agents" / "plugins"
    marketplace.mkdir(parents=True)
    (marketplace / "marketplace.yaml").write_text(
        "\n".join(('schema_version: "1"', "name: test-marketplace", "plugins:", *marketplace_entries, "")),
        encoding="utf-8",
    )
    subprocess.run(("git", "init", "--quiet", os.fspath(path)), check=True)
    if commit:
        _commit(path)
    return path


def _commit(repository: Path) -> None:
    subprocess.run(("git", "-C", os.fspath(repository), "add", "."), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            os.fspath(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "test content",
        ),
        check=True,
    )


@pytest.mark.anyio
async def test_manifest_is_the_only_authority_for_editable_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "content-plugins")
    installed = await store.install(os.fspath(repository))
    root = Path(installed.path)
    assert root == store.root / "plugin-reviewer"
    manifest = root / ".a13n-plugin" / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("Content for reviewer.", "My local description."))
    listed = await store.list()
    assert listed[0].description == "My local description."
    assert listed[0].commit == installed.commit
    assert set(json.loads((root / ".a13n-plugin" / "origin.json").read_text())) == {"repository", "commit"}


@pytest.mark.anyio
async def test_bad_plugin_and_bad_subagent_do_not_hide_healthy_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer", "plugin-writer"))
    store = ContentPluginStore(tmp_path / "content-plugins")
    first = await store.install(os.fspath(repository), plugin_id="plugin-reviewer")
    second = await store.install(os.fspath(repository), plugin_id="plugin-writer")
    (Path(first.path) / ".a13n-plugin" / "plugin.yaml").write_text("invalid manifest")
    (Path(second.path) / "subagents" / "bad.md").write_bytes(b"\xff")
    config = _configuration(tmp_path / "config")
    loaded = await load_agent_ui_configuration(config, content_plugin_root=store.root)
    assert [p.plugin_id for p in loaded.content_plugins] == ["plugin-writer"]
    assert "subagent-explorer" in loaded.subagents
    assert len(loaded.content_plugin_diagnostics) == 2
    # Deletion is possible even when the manifest cannot be parsed.
    await store.uninstall(first.plugin_id)
    assert not Path(first.path).exists()
    assert Path(second.path).is_dir()


@pytest.mark.anyio
async def test_empty_and_extra_files_are_valid_editable_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "content-plugins")
    installed = await store.install(os.fspath(repository))
    root = Path(installed.path)
    (root / "skills" / "notes.txt").write_text("Notes, not a Skill.")
    (root / "subagents" / "notes.txt").write_text("Notes, not a subagent.")
    (root / "subagents" / "explorer.md").unlink()
    listed = await store.list()
    assert listed[0].subagent_paths == ()
    assert listed[0].skills_path is not None


@pytest.mark.anyio
async def test_loading_does_not_read_supporting_asset_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_ui.content_plugins as plugins

    repository = _repository(tmp_path / "repository", ("plugin-reviewer",))
    store = ContentPluginStore(tmp_path / "content-plugins")
    installed = await store.install(os.fspath(repository))
    asset = Path(installed.path) / "skills" / "review" / "asset.bin"
    asset.write_bytes(b"x" * 1024)
    read = plugins._read_regular_file

    def guarded_read(path: Path, limit: int) -> bytes:
        assert path != asset
        return read(path, limit)

    monkeypatch.setattr(plugins, "_read_regular_file", guarded_read)
    assert len(await store.list()) == 1
    assert await store.fingerprint()


@pytest.mark.anyio
async def test_uninstall_removes_symlink_not_its_target(tmp_path: Path) -> None:
    store = ContentPluginStore(tmp_path / "content-plugins")
    store.root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("Keep")
    link = store.root / "plugin-linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    await store.uninstall("plugin-linked")
    assert not link.is_symlink()
    assert (outside / "keep.txt").read_text() == "Keep"
