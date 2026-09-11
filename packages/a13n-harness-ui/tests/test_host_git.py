from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from a13n_harness_ui import host_git
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_git import GitCaptureRequest, GitDiffRequest, HostGit


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=root,
        capture_output=True,
        check=check,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return result.stdout.decode("utf-8").strip()


def repository(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Git Tests")
    git(root, "config", "user.email", "git-tests@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.autocrlf", "false")
    return root


def commit(root: Path) -> None:
    git(root, "add", "--all")
    git(root, "commit", "-qm", "fixture")


@pytest.mark.anyio
async def test_discovery_unborn_nested_worktree_bare_and_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = HostGit(enabled=True)
    assert (await service.discover(str(tmp_path))).state == "not_repository"
    root = repository(tmp_path / "repo")
    found = (await service.discover(str(root))).repository
    assert found is not None and found.head_oid is None and found.branch == "main"
    assert Path(found.root).samefile(root)
    assert (await service.discover(str(root / ".git"))).state == "not_repository"
    (root / "file").write_text("initial\n")
    commit(root)
    nested = root / "directory"
    nested.mkdir()
    assert Path((await service.discover(str(nested))).repository.root).samefile(root)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong"))
    assert Path((await service.discover(str(root / "file"))).repository.root).samefile(root)
    child = repository(root / "child")
    assert Path((await service.discover(str(child))).repository.root).samefile(child)
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "--detach", str(linked))
    worktree = (await service.discover(str(linked))).repository
    assert worktree is not None and worktree.branch is None and worktree.head_oid == git(root, "rev-parse", "HEAD")
    assert Path(worktree.root).samefile(linked)
    assert Path(worktree.common_dir).samefile(root / ".git")
    assert not Path(worktree.git_dir).samefile(root / ".git")
    bare = tmp_path / "bare"
    git(tmp_path, "clone", "--bare", str(root), str(bare))
    assert (await service.discover(str(bare))).state == "bare"
    with pytest.raises(HarnessUiError) as error:
        await service.status(str(bare))
    assert error.value.code == "host_git_not_repository"


@pytest.mark.anyio
async def test_status_axes_paging_ignored_rename_delete_and_literals(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    (root / "modified").write_text("base\n")
    (root / "deleted").write_text("delete\n")
    (root / "old").write_text("rename me\n")
    (root / ".gitignore").write_text("ignored\n")
    commit(root)
    (root / "modified").write_text("staged\n")
    git(root, "add", "modified")
    (root / "modified").write_text("unstaged\n")
    (root / "deleted").unlink()
    git(root, "mv", "old", "new")
    (root / "ignored").write_text("ignored\n")
    (root / "untracked").write_text("new\n")
    service = HostGit(enabled=True)
    status = await service.status(str(root))
    entries = {item.path: item for item in status.entries}
    assert "ignored" not in entries
    assert (entries["modified"].index_status, entries["modified"].worktree_status) == ("M", "M")
    assert entries["new"].original_path == "old"
    assert entries["deleted"].worktree_status == "D"
    assert entries["untracked"].kind == "untracked"
    assert any(item.kind == "ignored" for item in (await service.status(str(root), include_ignored=True)).entries)
    first = await service.status(str(root), limit=1)
    assert first.next_offset == 1
    second = await service.status(str(root), limit=1, offset=1, expected_revision=first.revision)
    assert second.entries == status.entries[1:2]
    (root / "another").write_text("new")
    with pytest.raises(HarnessUiError, match="refresh"):
        await service.status(str(root), offset=1, expected_revision=first.revision)
    for comparison, included, excluded in [("staged", "+staged", "+unstaged"), ("unstaged", "+unstaged", "+staged")]:
        result = await service.diff(GitDiffRequest(repository_path=str(root), path="modified", comparison=comparison))
        assert included in result.text and excluded not in result.text
    renamed = await service.diff(GitDiffRequest(repository_path=str(root), path="new", comparison="staged"))
    assert renamed.original_path == "old" and "rename from old" in renamed.text
    deleted = await service.diff(GitDiffRequest(repository_path=str(root), path="deleted"))
    assert "deleted file mode" in deleted.text and "-delete" in deleted.text
    # Filenames are literal, including pathspec-like prefixes and line separators.
    names = ["-option", "space name", "unicode-你好"]
    if os.name != "nt":
        names += [":(glob)*", "line\nbreak", "tab\tname", "[wild]*", "back\\slash"]
    for name in names:
        (root / name).write_text("literal selection\n")
        preview = await service.diff(GitDiffRequest(repository_path=str(root), path=name, comparison="untracked"))
        assert "+literal selection" in preview.text
        assert preview.path == name
        git(root, "--literal-pathspecs", "add", "--", name)
        staged = await service.diff(GitDiffRequest(repository_path=str(root), path=name, comparison="staged"))
        assert "+literal selection" in staged.text and staged.path == name


@pytest.mark.anyio
async def test_diff_capture_revision_and_selection_are_detached(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    source = root / "file"
    source.write_text("unborn\n")
    git(root, "add", "file")
    service = HostGit(enabled=True)
    request = GitDiffRequest(repository_path=str(root), path="file", comparison="staged")
    unborn = await service.diff(request)
    assert unborn.repository.head_oid is None and "new file mode" in unborn.text
    capture = GitCaptureRequest(**request.model_dump(exclude={"expected_revision"}), expected_revision=unborn.revision)
    whole = await service.capture(capture)
    assert whole.data.decode() == unborn.text and whole.source.kind == "git_diff"
    selected = await service.capture(capture.model_copy(update={"start_line": 1, "end_line": 2}))
    assert selected.data.decode() == "".join(unborn.text.splitlines(keepends=True)[:2])
    source.write_text("worktree changed only\n")
    assert (await service.capture(capture)).data == whole.data
    git(root, "add", "file")
    with pytest.raises(HarnessUiError, match="reviewed comparison changed"):
        await service.capture(capture)
    commit(root)
    source.write_text("reviewed unstaged\n")
    unstaged = await service.diff(request.model_copy(update={"comparison": "unstaged"}))
    source.write_text("external edit\n")
    with pytest.raises(HarnessUiError, match="reviewed comparison changed"):
        await service.capture(
            GitCaptureRequest(repository_path=str(root), path="file", expected_revision=unstaged.revision)
        )
    assert b"unborn" in whole.data and b"external edit" not in whole.data
    with pytest.raises(HarnessUiError, match="range exceeds"):
        current = await service.diff(request.model_copy(update={"comparison": "unstaged"}))
        await service.capture(
            GitCaptureRequest(
                repository_path=str(root), path="file", expected_revision=current.revision, start_line=1, end_line=999
            )
        )


@pytest.mark.anyio
async def test_conflict_submodule_and_binary(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    (root / "file").write_text("base\n")
    commit(root)
    git(root, "checkout", "-qb", "other")
    (root / "file").write_text("other\n")
    commit(root)
    git(root, "checkout", "-q", "main")
    (root / "file").write_text("main\n")
    commit(root)
    git(root, "merge", "other", check=False)
    service = HostGit(enabled=True)
    status = await service.status(str(root))
    assert status.entries[0].kind == "conflicted"
    conflict = await service.diff(GitDiffRequest(repository_path=str(root), path="file"))
    assert "<<<<<<<" in conflict.text and "diff --cc" in conflict.text
    git(root, "merge", "--abort")
    child = repository(tmp_path / "child")
    (child / "file").write_text("child\n")
    commit(child)
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "sub")
    commit(root)
    sub = root / "sub"
    assert (sub / ".git").is_file()
    assert Path((await service.discover(str(sub))).repository.root).samefile(sub)
    (sub / "file").write_text("dirty submodule\n")
    status = await service.status(str(root))
    assert status.entries[0].path == "sub" and status.entries[0].submodule is not None
    assert "-dirty" in (await service.diff(GitDiffRequest(repository_path=str(root), path="sub"))).text
    (root / "binary").write_bytes(b"\x00\xffdata")
    binary = await service.diff(GitDiffRequest(repository_path=str(root), path="binary", comparison="untracked"))
    assert binary.presentation == "binary" and binary.text is None
    with pytest.raises(HarnessUiError, match="use Files"):
        await service.capture(
            GitCaptureRequest(
                repository_path=str(root), path="binary", comparison="untracked", expected_revision=binary.revision
            )
        )


@pytest.mark.anyio
async def test_query_is_read_only_and_disables_diff_helpers(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    source = root / "file"
    source.write_text("base\n")
    (root / ".gitattributes").write_text("file diff=custom\n")
    commit(root)
    source.write_text("edited\n")
    # A failing helper would make the query fail if Git tried to execute it.
    git(root, "config", "diff.external", "missing-external-diff-command")
    git(root, "config", "diff.custom.textconv", "missing-textconv-command")
    git(root, "config", "core.fsmonitor", "missing-fsmonitor-command")
    index = root / ".git/index"
    before = index.read_bytes(), index.stat().st_mtime_ns, git(root, "rev-parse", "HEAD")
    service = HostGit(enabled=True)
    assert (await service.status(str(root))).entries
    assert "+edited" in (await service.diff(GitDiffRequest(repository_path=str(root), path="file"))).text
    assert before == (index.read_bytes(), index.stat().st_mtime_ns, git(root, "rev-parse", "HEAD"))
    assert not (root / ".git/index.lock").exists()
    assert source.read_text() == "edited\n"


@pytest.mark.anyio
async def test_bounds_errors_and_index_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path / "repo")
    (root / "file").write_text("base\n")
    (root / "dir").mkdir()
    (root / "dir/child").write_text("child\n")
    commit(root)
    service = HostGit(enabled=True)
    for path in ("../outside", "/absolute", ".", "dir/../file", "file\x00"):
        with pytest.raises(HarnessUiError) as error:
            await service.diff(GitDiffRequest(repository_path=str(root), path=path))
        assert error.value.code == "host_git_path_invalid"
    for path in ("dir", "absent"):
        with pytest.raises(HarnessUiError) as error:
            await service.diff(GitDiffRequest(repository_path=str(root), path=path))
        assert error.value.code == "host_git_selection_invalid"
    assert (await service.diff(GitDiffRequest(repository_path=str(root), path="file"))).presentation == "unchanged"
    with pytest.raises(HarnessUiError) as error:
        await HostGit().discover(str(root))
    assert error.value.code == "host_git_disabled"
    (root / "file").write_text("x" * 5000)
    monkeypatch.setattr(host_git, "MAX_GIT_BYTES", 2048)
    with pytest.raises(HarnessUiError) as error:
        await service.diff(GitDiffRequest(repository_path=str(root), path="file"))
    assert error.value.code == "host_git_too_large"
    monkeypatch.setattr(host_git, "MAX_GIT_BYTES", 2 * 1024 * 1024)
    original_git = host_git._git

    async def changing_git(directory, *arguments, **kwargs):
        result = await original_git(directory, *arguments, **kwargs)
        if arguments[0] == "diff":
            git(root, "add", "file")
        return result

    monkeypatch.setattr(host_git, "_git", changing_git)
    with pytest.raises(HarnessUiError, match="index entries changed"):
        await service.diff(GitDiffRequest(repository_path=str(root), path="file"))
    monkeypatch.setattr(host_git, "_git", original_git)
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    assert not service.available
    with pytest.raises(HarnessUiError) as error:
        await service.discover(str(root))
    assert error.value.code == "host_git_unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["timeout", "cancel", "overflow"])
async def test_git_process_is_bounded_and_reaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    processes = []
    started = anyio.Event()
    real_open = anyio.open_process

    async def start(*args, **kwargs):
        script = "import time; time.sleep(60)"
        if action == "overflow":
            script = "import sys,time; sys.stdout.write('x'*10000); sys.stdout.flush(); time.sleep(60)"
        process = await real_open([sys.executable, "-c", script], cwd=tmp_path)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(host_git, "open_process", start)
    monkeypatch.setattr(host_git, "MAX_GIT_BYTES", 1024)
    if action == "timeout":
        monkeypatch.setattr(host_git, "GIT_TIMEOUT_SECONDS", 0.1)
    with anyio.fail_after(5):
        if action == "cancel":
            async with anyio.create_task_group() as group:
                group.start_soon(host_git._git, tmp_path, "status")
                await started.wait()
                group.cancel_scope.cancel()
        else:
            with pytest.raises(HarnessUiError) as error:
                await host_git._git(tmp_path, "status")
            assert error.value.code == ("host_git_timeout" if action == "timeout" else "host_git_too_large")
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.anyio
@pytest.mark.skipif(os.name == "nt", reason="Native symlink and executable-bit semantics")
async def test_symlinks_modes_and_nonregular_untracked_selection(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    source = root / "file"
    source.write_text("base\n")
    commit(root)
    source.chmod(0o755)
    service = HostGit(enabled=True)
    mode = await service.diff(GitDiffRequest(repository_path=str(root), path="file"))
    assert "new mode 100755" in mode.text
    outside = tmp_path / "outside"
    outside.write_text("must not follow\n")
    link = root / "link"
    link.symlink_to(outside)
    diff = await service.diff(GitDiffRequest(repository_path=str(root), path="link", comparison="untracked"))
    assert str(outside) in diff.text and "must not follow" not in diff.text
    link.unlink()
    link.symlink_to(tmp_path / "missing")
    dangling = await service.diff(GitDiffRequest(repository_path=str(root), path="link", comparison="untracked"))
    assert str(tmp_path / "missing") in dangling.text
    os.mkfifo(root / "pipe")
    with pytest.raises(HarnessUiError) as error:
        await service.diff(GitDiffRequest(repository_path=str(root), path="pipe", comparison="untracked"))
    assert error.value.code in {"host_git_selection_invalid", "host_git_conflict"}


@pytest.mark.anyio
async def test_status_limits_and_revision_preconditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path / "repo")
    service = HostGit(enabled=True)
    for name in ("one", "two"):
        (root / name).write_text("new\n")
    with pytest.raises(HarnessUiError) as error:
        await service.status(str(root), offset=1)
    assert error.value.code == "host_git_conflict"
    for kwargs in ({"limit": 501}, {"offset": -1}):
        with pytest.raises(HarnessUiError) as error:
            await service.status(str(root), **kwargs)
        assert error.value.code == "host_git_range_invalid"
    monkeypatch.setattr(host_git, "MAX_GIT_ENTRIES", 1)
    with pytest.raises(HarnessUiError) as error:
        await service.status(str(root))
    assert error.value.code == "host_git_too_large"


@pytest.mark.anyio
async def test_git_failure_is_not_an_empty_status(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    (root / "file").write_text("base\n")
    commit(root)
    (root / ".git/index").write_bytes(b"broken index")
    with pytest.raises(HarnessUiError) as error:
        await HostGit(enabled=True).status(str(root))
    assert error.value.code == "host_git_failed"


def test_git_openapi_exposes_required_capture_revision_without_app() -> None:
    from a13n_harness_ui.webui import create_webui, openapi_document

    def forbidden():
        raise AssertionError("OpenAPI must not open an App or native Git")

    document = openapi_document(create_webui(forbidden, api_key="key"))
    for route in ("repository", "status", "diff"):
        assert "get" in document["paths"][f"/api/host/git/{route}"]
    capture = document["paths"]["/api/threads/{thread_id}/host-git-captures"]["post"]
    assert capture["requestBody"]["required"]
    assert "expected_revision" in document["components"]["schemas"]["GitCaptureRequest"]["required"]
    source = document["components"]["schemas"]["ThreadAttachment"]["properties"]["source"]
    assert len(source["anyOf"]) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["dir", "你好", "literal[dir]"])
async def test_staged_file_replacing_directory_does_not_capture_descendants(tmp_path: Path, name: str) -> None:
    root = repository(tmp_path / "repo")
    directory = root / name
    directory.mkdir()
    (directory / "a").write_text("unselected a\n")
    (directory / "b").write_text("unselected b\n")
    commit(root)
    git(root, "rm", "-r", name)
    directory.write_text("selected replacement\n")
    git(root, "add", name)
    service = HostGit(enabled=True)
    request = GitDiffRequest(repository_path=str(root), path=name, comparison="staged")
    preview = await service.diff(request)
    assert "+selected replacement" in preview.text
    assert "unselected" not in preview.text
    captured = await service.capture(
        GitCaptureRequest(repository_path=str(root), path=name, comparison="staged", expected_revision=preview.revision)
    )
    assert captured.data.decode() == preview.text


@pytest.mark.anyio
@pytest.mark.parametrize("destination", ["new", "old/new"])
async def test_staged_rename_can_leave_directory_at_original_path(tmp_path: Path, destination: str) -> None:
    root = repository(tmp_path / "repo")
    (root / "old").write_text("renamed content\n")
    commit(root)
    (root / "old").rename(root / "intermediate")
    (root / "old").mkdir()
    (root / "intermediate").rename(root / destination)
    (root / "old/child").write_text("unselected child\n")
    git(root, "add", "--all")
    preview = await HostGit(enabled=True).diff(
        GitDiffRequest(repository_path=str(root), path=destination, comparison="staged")
    )
    assert preview.original_path == "old"
    assert "rename from old" in preview.text and f"rename to {destination}" in preview.text
    assert "unselected" not in preview.text
