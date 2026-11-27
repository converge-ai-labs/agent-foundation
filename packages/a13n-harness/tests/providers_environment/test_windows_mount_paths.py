import pytest
from a13n_harness.environment._mount_path import (
    mount_path_from_provider_path,
    operation_path_parent,
    provider_path_suffix,
)


@pytest.mark.parametrize(
    "path,parent",
    [
        ("C:/one.txt", "C:/"),
        ("C:/dir/one.txt", "C:/dir"),
        ("//server/share/one.txt", "//server/share/"),
        ("/C:/one.txt", "/C:/"),
        ("/UNC/server/share/one.txt", "/UNC/server/share/"),
        ("/dir/one.txt", "/dir"),
        ("one.txt", ""),
    ],
)
def test_operation_parent_keeps_windows_anchors(path, parent):
    assert operation_path_parent(path) == parent


@pytest.mark.parametrize(
    "root,path,expected",
    [
        ("/C:/", "/c:/One.txt", ("One.txt",)),
        ("/c:/work", "/C:/Work/One.txt", ("One.txt",)),
        ("/C:/", "/c:/", ()),
        ("/UNC/Server/Share/", "/UNC/server/share/One.txt", ("One.txt",)),
        ("/UNC/server/Share", "/UNC/Server/share/", ()),
        ("/UNC", "/UNC/server/share/file", ("server", "share", "file")),
    ],
)
def test_windows_provider_suffix_keeps_spelling_below_case_insensitive_root(root, path, expected):
    assert provider_path_suffix(path, root) == expected
    assert mount_path_from_provider_path("/workspace", path, root) == "/".join(("/workspace", *expected))


@pytest.mark.parametrize("root,path", [("/Work", "/work/file"), ("/c:/work", "/C:/workbench/file")])
def test_provider_suffix_requires_matching_components(root, path):
    with pytest.raises(ValueError, match="outside"):
        provider_path_suffix(path, root)


@pytest.fixture
async def windows_mount(tmp_path, provider_root, mount_path):
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.environment.sources import EnvironmentMount
    from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL

    from ..environment_helpers import Source
    from ..test_environment_connectors import bind

    class BootstrapSource(Source):
        defer_bootstrap = True
        bootstrap_snapshot = None

        def observe_bootstrap(self, key, capture):
            raise AssertionError("building a bootstrap context does not observe files")

    source = BootstrapSource(DIRECT_LOCAL.execution_connector({"root": {"path": str(tmp_path)}}))
    runtime = create_environment_runtime(
        mounts={"workspace": EnvironmentMount(source, mount_path=mount_path, provider_root=provider_root)},
        default_mount="workspace",
    )
    async with bind(runtime) as environment:
        yield environment
    assert source.ready_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mount_path,provider_root",
    [("C:/", "/C:/"), ("//server/share/", "/UNC/server/share/"), ("/workspace", "/C:/work")],
)
async def test_windows_bootstrap_keeps_root_and_relative_path(windows_mount, mount_path, provider_root):
    from a13n_harness.environment.models import EnvironmentAction

    actions = frozenset({EnvironmentAction.FILE_READ_TEXT})
    root = windows_mount.bootstrap_context(mount_path, actions)
    assert root is not None and root.path == "." and root.virtual_path == mount_path and root.deferred
    child = mount_path.rstrip("/") + "/note.txt"
    context = windows_mount.bootstrap_context(child, actions)
    assert context is not None and context.path == "note.txt" and context.virtual_path == child
    assert windows_mount.select_files(mount_path).resolved_path.path == provider_root


@pytest.mark.anyio
@pytest.mark.parametrize("mount_path,provider_root", [("C:/", "/C:/"), ("//server/share/", "/UNC/server/share/")])
async def test_windows_file_resources_and_skills_keep_anchor_separator(
    windows_mount, mount_path, provider_root, monkeypatch
):
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.skills import FileSkillSource, _PinnedSkillFileOperator, _SkillFileRoute
    from a13n_harness.environment.files import FileTextResult
    from a13n_harness.toolsets.files import FileToolset

    tools = FileToolset(windows_mount.files, file_scopes=windows_mount)
    access = tools._file_access
    skill_path = mount_path + "SKILL.md"
    # Resolving the file is the only resource operation which may prepare the mount.
    resolver = access.resource_resolver("path", include_parent=True)
    assert resolver is not None
    monkeypatch.setattr(windows_mount, "resolve_files", AsyncMock(side_effect=windows_mount.select_files))
    _resource, parent = await resolver({"path": skill_path}, context=None)
    assert parent.identifier.endswith(":" + provider_root)
    selection = windows_mount.select_files(skill_path)
    files = AsyncMock()
    token = access._selection.set(selection)
    try:
        assert access.has_mount_root_parent(skill_path)
        await tools._ensure_parent(files, skill_path)
    finally:
        access._selection.reset(token)
    files.mkdir.assert_not_awaited()

    source = FileSkillSource("test", (mount_path,))
    item = source._catalog_entry(
        FileTextResult(
            path=skill_path,
            text="---\nname: test-skill\ndescription: Test\n---\n",
            line_offset=0,
            lines_read=4,
            has_more=False,
        )
    )
    assert item.path == mount_path
    pinned = _PinnedSkillFileOperator((_SkillFileRoute(mount_path, windows_mount.select_files(mount_path), files),), {})
    assert pinned.resolve_path(mount_path).path == provider_root
    assert pinned.resolve_path(skill_path).path == provider_root + "SKILL.md"
