"""Metadata tree budgets, breadth-first order, and routed path accounting."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.capabilities.context import WorkspaceOutlineConfiguration, _scan_workspace_outline
from a13n_harness.environment import EnvironmentError, FileTreeRequest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.trees import encoded_path_prefix_bytes, list_tree, tree_entry_bytes
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.eip.files import EIPFileOperator

from .test_document_discovery import _files, _instance
from .test_skills import _runtime_mount

pytestmark = pytest.mark.anyio


def seed(root):
    for directory in ("a", "z", ".hidden"):
        (root / directory).mkdir()
        (root / directory / "child").write_text("never read")
    (root / "a.txt").write_text("never read")
    (root / "link").symlink_to("a", target_is_directory=True)


async def test_local_tree_bfs_depth_hidden_and_metadata_only(tmp_path, monkeypatch):
    seed(tmp_path)
    files = _files(tmp_path)
    monkeypatch.setattr(files, "read_text", AsyncMock(side_effect=AssertionError("content read")))
    result = await files.list_tree(FileTreeRequest(root="/"))
    assert [entry.path for entry in result.entries] == ["/a", "/a.txt", "/link", "/z", "/a/child", "/z/child"]
    assert not result.truncated and result.directory_calls == 3
    shallow = await files.list_tree(FileTreeRequest(root="/", max_depth=1, include_hidden=True))
    assert shallow.truncated and shallow.directory_calls == 1
    assert shallow.entries[0].path == "/.hidden"


@pytest.mark.parametrize("budget", ["bytes", "entries", "calls"])
async def test_tree_stops_before_another_directory_call(tmp_path, monkeypatch, budget):
    seed(tmp_path)
    files = _files(tmp_path)
    original = files.list
    calls = []

    async def observed(path, **kwargs):
        calls.append(path)
        return await original(path, **kwargs)

    monkeypatch.setattr(files, "list", observed)
    options = (
        {"max_bytes": 1}
        if budget == "bytes"
        else {"max_entries": 1}
        if budget == "entries"
        else {"max_directory_calls": 1}
    )
    result = await files.list_tree(FileTreeRequest(root="/", **options))
    assert result.truncated and result.directory_calls == 1
    assert calls == ["/"]
    assert len(result.entries) == (0 if budget == "bytes" else 1 if budget == "entries" else 4)


async def test_tree_continuation_finishes_parent_before_children(tmp_path, monkeypatch):
    seed(tmp_path)
    files = _files(tmp_path)
    original = files.list

    async def small_page(path, **kwargs):
        return await original(path, **{**kwargs, "max_results": 1})

    monkeypatch.setattr(files, "list", small_page)
    result = await files.list_tree(FileTreeRequest(root="/"))
    assert [entry.path for entry in result.entries] == ["/a", "/a.txt", "/link", "/z", "/a/child", "/z/child"]
    assert result.directory_calls == 6 and not result.truncated


async def test_virtual_tree_accounts_escaped_logical_prefix_before_descent(tmp_path):
    seed(tmp_path)
    logical = '/long-中-"-root'
    runtime = create_environment_runtime(mounts={"root": replace(_runtime_mount(tmp_path), mount_path=logical)})
    async with runtime.bind(thread_id="thread", run_id="run", instance=_instance(), host_refs={}) as env:
        request = FileTreeRequest(root=logical, max_directory_calls=1)
        initial = await env.files.list_tree(request)
        cost = tree_entry_bytes(initial.entries[0], logical, encoded_path_prefix_bytes(logical))
        exact = await env.files.list_tree(request.model_copy(update={"max_bytes": cost}))
        short = await env.files.list_tree(request.model_copy(update={"max_bytes": cost - 1}))
        assert exact.entries == initial.entries[:1] and exact.truncated
        assert short.entries == () and short.truncated
        content = await _scan_workspace_outline(env.files, WorkspaceOutlineConfiguration(root=logical, max_bytes=512))
        assert len(content.encode()) <= 512 and "long-中" in content


@pytest.mark.parametrize("name", ["long-" + "x" * 200, "t"])
async def test_virtual_tree_accounts_canonical_selected_link_root(tmp_path, name):
    from a13n_harness._json import dump_json_bytes
    from a13n_harness.environment.trees import tree_entry_value

    target = tmp_path / name
    target.mkdir()
    for index in range(10):
        (target / f"file-{index}").touch()
    link = "selected-link"
    (tmp_path / link).symlink_to(target, target_is_directory=True)
    runtime = create_environment_runtime(mounts={"root": replace(_runtime_mount(tmp_path), mount_path="/repo")})
    async with runtime.bind(thread_id="thread", run_id="run", instance=_instance(), host_refs={}) as env:
        root = f"/repo/{link}"
        result = await env.files.list_tree(FileTreeRequest(root=root, max_bytes=100))
        assert result.truncated
        assert sum(len(dump_json_bytes(tree_entry_value(entry))) + 1 for entry in result.entries) <= 100
        content = await _scan_workspace_outline(env.files, WorkspaceOutlineConfiguration(root=root, max_bytes=512))
        assert len(content.encode()) <= 512
        assert f"/repo/{name}/file-0" in content


async def test_unadvertised_tree_uses_explicit_compatibility_but_native_failure_does_not(tmp_path, monkeypatch):
    seed(tmp_path)
    local = _files(tmp_path)
    session = SimpleNamespace(descriptor=SimpleNamespace(working_directory="/", available_methods=()))
    files = EIPFileOperator(session=session, environment_id="test", execution_id="test", generation="1")
    monkeypatch.setattr(files, "list", local.list)
    assert await files.list_tree(FileTreeRequest(root="/")) == await list_tree(local, FileTreeRequest(root="/"))
    session.descriptor.available_methods = ("file.tree",)
    session.max_response_bytes = 1024 * 1024
    session.client = SimpleNamespace(
        file_tree=AsyncMock(side_effect=EnvironmentError("failed", code="environment_provider_failure"))
    )
    monkeypatch.setattr(files, "list", AsyncMock(side_effect=AssertionError("fallback after failure")))
    with pytest.raises(EnvironmentError, match="failed"):
        await files.list_tree(FileTreeRequest(root="/"))


async def test_aggregate_tree_routes_descendant_mounts(tmp_path, monkeypatch):
    parent, child = tmp_path / "parent", tmp_path / "child"
    (parent / "a").mkdir(parents=True)
    (parent / "a/shadow").write_text("backing")
    child.mkdir()
    (child / "visible").write_text("child")
    runtime = create_environment_runtime(
        mounts={
            "parent": replace(_runtime_mount(parent), mount_path="/repo"),
            "child": replace(_runtime_mount(child, environment_id="child"), mount_path="/repo/a"),
        }
    )
    monkeypatch.setattr(
        LocalFileOperator, "list_tree", AsyncMock(side_effect=AssertionError("ancestor native traversal"))
    )
    async with runtime.bind(thread_id="thread", run_id="run", instance=_instance(), host_refs={}) as env:
        result = await env.files.list_tree(FileTreeRequest(root="/repo"))
    assert [entry.path for entry in result.entries] == ["/repo/a", "/repo/a/visible"]
