"""Document discovery preserves file authority and removes redundant catalog reads."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentIdentityRef, AgentInstanceContext, DefinitionError
from a13n_harness.capabilities import FileSkillSource, SkillCatalogItem, SkillManager
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
    FileDocumentFailure,
    FileDocumentRequest,
    FileDocumentText,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator

from .environment_helpers import DirectLocalFilePolicy
from .test_skills import _runtime_mount

pytestmark = pytest.mark.anyio


def _files(root):
    return LocalFileOperator(
        root=root, policy=DirectLocalFilePolicy(max_value_bytes=2 * 1024 * 1024), execution_id="test", generation="1"
    )


def _skill(root: Path, directory: str, name: str) -> None:
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A skill.\n---\n")


def _instance():
    return AgentInstanceContext(identity=AgentIdentityRef(issuer="test", subject="discovery"), agent_instance_id="scan")


async def test_local_discovery_observes_links_errors_and_bounds(tmp_path):
    _skill(tmp_path, "skills", "root")
    _skill(tmp_path, "skills/z", "last")
    _skill(tmp_path, "skills/.hidden", "hidden")
    _skill(tmp_path, "skills/z/nested", "nested")
    (tmp_path / "skills/a").mkdir()
    (tmp_path / "skills/a/SKILL.md").symlink_to("../z/SKILL.md")
    (tmp_path / "skills/b").mkdir()
    (tmp_path / "skills/b/SKILL.md").write_bytes(b"\xff")
    (tmp_path / "skills/link").symlink_to("z", target_is_directory=True)
    files = _files(tmp_path)
    outcomes = [
        item async for item in files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md"))
    ]
    assert [item.path if isinstance(item, FileDocumentFailure) else item.info.path for item in outcomes] == [
        "/skills/SKILL.md",
        "/skills/a/SKILL.md",
        "/skills/b/SKILL.md",
        "/skills/z/SKILL.md",
    ]
    assert isinstance(outcomes[1], FileDocumentText)
    assert outcomes[1].info.kind == "file"
    assert outcomes[1].info.size == (tmp_path / "skills/z/SKILL.md").stat().st_size
    assert isinstance(outcomes[2], FileDocumentFailure)
    assert outcomes[2].phase == "read"
    assert outcomes[2].error.code == "environment_unsupported"
    iterator = files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md", max_entries=2))
    with pytest.raises(EnvironmentError) as failure:
        await anext(iterator)
    assert failure.value.details == {"reason": "entries_exceeded"}


@pytest.mark.parametrize("linked_parent", [False, True])
async def test_local_discovery_preserves_selected_root_through_directory_links(tmp_path, linked_parent):
    _skill(tmp_path, "actual/skills", "root")
    _skill(tmp_path, "actual/skills/a", "child")
    _skill(tmp_path, "actual/skills/.hidden", "hidden")
    _skill(tmp_path, "actual/skills/a/nested", "nested")
    (tmp_path / "actual/skills/link").symlink_to("a", target_is_directory=True)
    if linked_parent:
        (tmp_path / "selected").symlink_to("actual", target_is_directory=True)
        root = "/selected/skills"
    else:
        (tmp_path / "selected").symlink_to("actual/skills", target_is_directory=True)
        root = "/selected"
    files = _files(tmp_path)
    documents = [item async for item in files.discover_documents(FileDocumentRequest(root=root, basename="SKILL.md"))]

    assert [item.info.path for item in documents] == [f"{root}/SKILL.md", f"{root}/a/SKILL.md"]
    assert [item.prefix.path for item in documents] == [f"{root}/SKILL.md", f"{root}/a/SKILL.md"]
    assert [item.info.kind for item in documents] == ["file", "file"]
    assert "name: child" in documents[1].prefix.text
    catalog = await SkillManager((FileSkillSource("selected", (root,)),)).scan(files=files)
    assert [(item.name, item.path) for item in catalog] == [("child", f"{root}/a"), ("root", root)]


async def test_local_discovery_rejects_selected_root_link_outside_file_authority(tmp_path):
    _skill(tmp_path, "outside/skills/a", "outside")
    confined = tmp_path / "confined"
    confined.mkdir()
    (confined / "skills").symlink_to(tmp_path / "outside/skills", target_is_directory=True)
    files = _files(confined)

    with pytest.raises(EnvironmentError) as failure:
        await anext(files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md")))
    assert failure.value.code == "environment_denied"


async def test_file_catalog_reuses_discovered_metadata_but_custom_sources_are_checked(tmp_path, monkeypatch):
    _skill(tmp_path, "skills/a", "one")
    files = _files(tmp_path)
    forbidden = AsyncMock(side_effect=AssertionError("unexpected per-document operation"))
    monkeypatch.setattr(files, "stat", forbidden)
    monkeypatch.setattr(files, "read_text", forbidden)
    assert [item.name for item in await SkillManager((FileSkillSource("files", ("/skills",)),)).scan(files=files)] == [
        "one"
    ]
    forbidden.assert_not_called()

    async def catalog(*, files):
        return (SkillCatalogItem(name="one", description="A skill.", path="/skills/a"),)

    source = SimpleNamespace(source_id="custom", roots=("/skills",), catalog=catalog)
    check = AsyncMock(side_effect=EnvironmentError("gone", code="environment_not_found"))
    monkeypatch.setattr(files, "stat", check)
    with pytest.raises(DefinitionError) as failure:
        await SkillManager((source,)).scan(files=files)
    assert failure.value.code == "skill_path_unavailable"
    check.assert_awaited_once_with("/skills/a/SKILL.md")


async def test_aggregate_discovery_routes_nested_mount_before_document_io(tmp_path, monkeypatch):
    parent, child = tmp_path / "parent", tmp_path / "child"
    _skill(parent, "skills", "root")
    _skill(parent, "skills/a", "shadowed")
    _skill(child, "", "child")
    runtime = create_environment_runtime(
        mounts={
            "parent": replace(_runtime_mount(parent), mount_path="/repo"),
            "child": replace(_runtime_mount(child, environment_id="child"), mount_path="/repo/skills/a"),
        }
    )
    observed = []
    original = LocalFileOperator.read_text

    async def read(self, path, **kwargs):
        observed.append((self._root, path))
        return await original(self, path, **kwargs)

    monkeypatch.setattr(LocalFileOperator, "read_text", read)
    monkeypatch.setattr(LocalFileOperator, "discover_documents", lambda *args: pytest.fail("native ancestor scan"))
    async with runtime.bind(thread_id="thread", run_id="run", instance=_instance(), host_refs={}) as environment:
        documents = [
            item
            async for item in environment.files.discover_documents(
                FileDocumentRequest(root="/repo/skills", basename="SKILL.md")
            )
        ]
    assert [item.prefix.path for item in documents] == ["/repo/skills/SKILL.md", "/repo/skills/a/SKILL.md"]
    assert "name: child" in documents[1].prefix.text
    assert (parent.resolve(), "/skills/a/SKILL.md") not in observed
    assert (child.resolve(), "/SKILL.md") in observed


@pytest.mark.parametrize(
    "missing", [EnvironmentAction.FILE_LIST, EnvironmentAction.FILE_STAT, EnvironmentAction.FILE_READ_TEXT]
)
async def test_discovery_requires_each_compound_permission_before_dispatch(tmp_path, monkeypatch, missing):
    _skill(tmp_path, "skills/a", "one")
    mount = replace(
        _runtime_mount(tmp_path),
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction) - {missing}),
    )
    runtime = create_environment_runtime(mounts={"local": mount}, default_mount="local")
    monkeypatch.setattr(LocalFileOperator, "discover_documents", lambda *args: pytest.fail("unauthorized native scan"))
    async with runtime.bind(thread_id="thread", run_id="run", instance=_instance(), host_refs={}) as environment:
        with pytest.raises(EnvironmentError) as failure:
            await anext(
                environment.files.discover_documents(FileDocumentRequest(root="/workspace/skills", basename="SKILL.md"))
            )
    assert failure.value.code == "environment_denied"


async def test_eip_catalog_uses_one_native_page_and_no_final_stat(monkeypatch):
    from a13n_envd_client.eip import v1 as eip

    from .providers_environment.test_eip_files import discovery_fragment, discovery_operator

    text = "---\nname: native\ndescription: One native page.\n---\n"
    item = discovery_fragment(text)
    files, client = discovery_operator(
        [eip.FileDiscoverDocumentsResult(documents=(item,), entries_exceeded=False, omitted_unrepresentable_entries=0)]
    )
    forbidden = AsyncMock(side_effect=AssertionError("unexpected extra file RPC"))
    for method in ("list", "stat", "read_text"):
        monkeypatch.setattr(files, method, forbidden)
    catalog = await SkillManager((FileSkillSource("native", ("/skills",)),)).scan(files=files)
    assert [item.name for item in catalog] == ["native"]
    assert client.client.file_discover_documents.await_count == 1
    forbidden.assert_not_called()


async def test_local_discovery_keeps_protected_file_read_guard(tmp_path, monkeypatch):
    from a13n_harness.providers.environment.direct_local import files as local_files

    _skill(tmp_path, "skills", "private")

    def deny(path):
        raise EnvironmentError("protected", code="environment_denied")

    monkeypatch.setattr(local_files, "check_local_file_read", deny)
    outcomes = [
        item
        async for item in _files(tmp_path).discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md"))
    ]
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], FileDocumentFailure)
    assert outcomes[0].error.code == "environment_denied"
