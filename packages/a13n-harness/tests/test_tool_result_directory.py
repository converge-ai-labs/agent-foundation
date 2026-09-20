from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import RunBindings
from a13n_harness.environment import EnvironmentAction
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.tools._output import _ToolResultSpillStore

from .test_dynamic_environment import _local_mount

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("directory", ["", "relative/tmp", "/tmp/../results", "/tmp/", "/tmp/\x00", 42])
def test_tool_result_directory_requires_canonical_absolute_path(directory: Any) -> None:
    with pytest.raises(ValueError, match="tool_result_directory"):
        RunBindings.embedded(tool_result_directory=directory)


@pytest.mark.parametrize("root", [None, "/scratch", "C:/Scratch", "//server/share/scratch"])
async def test_explicit_spills_use_named_sink_without_default_and_preserve_other_files(tmp_path: Path, root) -> None:
    workspace, scratch = tmp_path / "workspace", tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    (scratch / "tmp/tool-results").mkdir(parents=True)
    sentinel = scratch / "tmp/tool-results/keep.txt"
    sentinel.write_text("keep")
    runtime = create_environment_runtime(
        mounts={"workspace": _local_mount(workspace), "scratch": _local_mount(scratch, mount_path=root)},
        default_mount=None,
    )
    expected_root = root or "/environment/scratch"
    bindings = RunBindings.embedded(environment=runtime, tool_result_directory=f"{expected_root}/tmp/tool-results")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        context = cast(
            Any, SimpleNamespace(run_id="run-1", environment=env, tool_result_directory=bindings.tool_result_directory)
        )
        store, other = _ToolResultSpillStore(context), _ToolResultSpillStore(context)
        first = await store.write(b"first", suffix=".txt")
        other_path = await other.write(b"other", suffix=".json")
        assert first and other_path and first != other_path
        assert first.startswith(f"{expected_root}/tmp/tool-results/run-")
        await runtime.set_default("workspace")
        second = await store.write(b"second", suffix=".json")
        assert second and second.rsplit("/", 1)[0] == first.rsplit("/", 1)[0]
        assert await env.files.read_bytes(first) == b"first"
        assert await env.files.read_bytes(second) == b"second"
        await store.close()
        assert await env.files.read_bytes(other_path) == b"other"
        await other.close()
        assert await store.write(b"closed", suffix=".txt") is None
    assert list(workspace.iterdir()) == []
    assert list((scratch / "tmp/tool-results").iterdir()) == [sentinel]
    assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("failure", ["missing", "read_only", "replaced"])
async def test_explicit_spill_failure_never_falls_back_or_cleans_replacement(tmp_path: Path, failure: str) -> None:
    workspace, scratch, replacement = (tmp_path / name for name in ("workspace", "scratch", "replacement"))
    for path in (workspace, scratch, replacement):
        path.mkdir()
    mounts = {"workspace": _local_mount(workspace)}
    if failure != "missing":
        mounts["scratch"] = _local_mount(
            scratch, operations=frozenset() if failure == "read_only" else frozenset(EnvironmentAction)
        )
    runtime = create_environment_runtime(mounts=mounts, default_mount="workspace")
    bindings = RunBindings.embedded(environment=runtime, tool_result_directory="/environment/scratch/tmp/tool-results")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=bindings.instance, host_refs={}) as env:
        await runtime._activate()
        store = _ToolResultSpillStore(
            cast(
                Any,
                SimpleNamespace(run_id="run-1", environment=env, tool_result_directory=bindings.tool_result_directory),
            )
        )
        path = await store.write(b"original", suffix=".txt")
        if failure == "replaced":
            assert path
            relative = path.removeprefix("/environment/scratch/")
            sentinel = replacement / relative
            sentinel.parent.mkdir(parents=True)
            sentinel.write_bytes(b"replacement")
            await runtime.replace("scratch", _local_mount(replacement))
            await store.close()
            assert (scratch / relative).read_bytes() == b"original"
            assert sentinel.read_bytes() == b"replacement"
        else:
            assert path is None
            await store.close()
    assert list(workspace.iterdir()) == []
