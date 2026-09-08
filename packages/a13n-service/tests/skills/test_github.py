from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable

import anyio
import httpx2
import pytest
from a13n_service.skills import GitHubRevisionSource
from a13n_service.skills.github import GitHubAcquisitionError, GitHubSkillAcquirer
from a13n_service.skills.package import SkillPackageError

SKILL_DOCUMENT = b"---\nname: deploy-helper\ndescription: Deploy a reviewed service.\n---\n"
COMMIT_SHA = "a" * 40
ROOT_TREE_SHA = "b" * 40
SKILLS_TREE_SHA = "c" * 40
PACKAGE_TREE_SHA = "d" * 40
SECRET_ID = "sec_1234567890abcdef"


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest()


def json_response(request: httpx2.Request, payload: object, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload, request=request)


def source(**updates: object) -> GitHubRevisionSource:
    values: dict[str, object] = {
        "repository_url": "https://github.com/Example/release-tools.git/",
        "ref": "main",
        "subdirectory": "skills/deploy",
        "expected_commit_sha": COMMIT_SHA,
        "credential_secret_id": SECRET_ID,
    }
    values.update(updates)
    return GitHubRevisionSource(**values)


def commit_payload() -> dict[str, object]:
    return {"sha": COMMIT_SHA, "commit": {"tree": {"sha": ROOT_TREE_SHA}}}


def tree_entry(path: str, *, entry_type: str, mode: str, sha: str, size: int | None = None) -> dict[str, object]:
    value: dict[str, object] = {"path": path, "type": entry_type, "mode": mode, "sha": sha}
    if size is not None:
        value["size"] = size
    return value


def blob_payload(content: bytes, *, sha: str | None = None, size: int | None = None) -> dict[str, object]:
    return {
        "sha": sha or git_blob_sha(content),
        "size": len(content) if size is None else size,
        "encoding": "base64",
        "content": base64.encodebytes(content).decode(),
    }


@pytest.mark.anyio
async def test_acquire_resolves_commit_subdirectory_and_exact_blob_content() -> None:
    runbook = b"reviewed steps"
    skill_sha = git_blob_sha(SKILL_DOCUMENT)
    runbook_sha = git_blob_sha(runbook)
    seen_authorization: list[str | None] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen_authorization.append(request.headers.get("authorization"))
        path = request.url.path
        if path.endswith("/commits/main"):
            return json_response(request, commit_payload())
        if path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return json_response(
                request,
                {
                    "tree": [tree_entry("skills", entry_type="tree", mode="040000", sha=SKILLS_TREE_SHA)],
                    "truncated": False,
                },
            )
        if path.endswith(f"/git/trees/{SKILLS_TREE_SHA}"):
            return json_response(
                request,
                {
                    "tree": [tree_entry("deploy", entry_type="tree", mode="040000", sha=PACKAGE_TREE_SHA)],
                    "truncated": False,
                },
            )
        if path.endswith(f"/git/trees/{PACKAGE_TREE_SHA}"):
            assert request.url.params.get("recursive") == "1"
            return json_response(
                request,
                {
                    "tree": [
                        tree_entry(
                            "SKILL.md", entry_type="blob", mode="100755", sha=skill_sha, size=len(SKILL_DOCUMENT)
                        ),
                        tree_entry("references", entry_type="tree", mode="040000", sha="e" * 40),
                        tree_entry(
                            "references/runbook.md",
                            entry_type="blob",
                            mode="100644",
                            sha=runbook_sha,
                            size=len(runbook),
                        ),
                    ],
                    "truncated": False,
                },
            )
        if path.endswith(f"/git/blobs/{skill_sha}"):
            return json_response(request, blob_payload(SKILL_DOCUMENT))
        if path.endswith(f"/git/blobs/{runbook_sha}"):
            return json_response(request, blob_payload(runbook))
        raise AssertionError(f"unexpected GitHub request: {request.url}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        acquired = await GitHubSkillAcquirer(client).acquire(source(), credential="github-token")

    assert acquired.package.manifest.skill_name == "deploy-helper"
    assert [item.path for item in acquired.package.files] == ["SKILL.md", "references/runbook.md"]
    assert acquired.provenance.repository_url == "https://github.com/Example/release-tools"
    assert acquired.provenance.requested_ref == "main"
    assert acquired.provenance.resolved_commit_sha == COMMIT_SHA
    assert acquired.provenance.subdirectory == "skills/deploy"
    assert set(seen_authorization) == {"Bearer github-token"}
    assert "github-token" not in acquired.provenance.model_dump_json()
    assert SECRET_ID not in acquired.provenance.model_dump_json()


@pytest.mark.anyio
async def test_absent_ref_selects_default_branch_head_from_bounded_commit_list() -> None:
    skill_sha = git_blob_sha(SKILL_DOCUMENT)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/commits"):
            assert request.url.params.get("per_page") == "1"
            return json_response(request, [commit_payload()])
        if path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return json_response(
                request,
                {
                    "tree": [
                        tree_entry(
                            "SKILL.md",
                            entry_type="blob",
                            mode="100644",
                            sha=skill_sha,
                            size=len(SKILL_DOCUMENT),
                        )
                    ],
                    "truncated": False,
                },
            )
        if path.endswith(f"/git/blobs/{skill_sha}"):
            return json_response(request, blob_payload(SKILL_DOCUMENT))
        raise AssertionError(f"unexpected GitHub request: {request.url}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        acquired = await GitHubSkillAcquirer(client).acquire(
            source(ref=None, subdirectory="", expected_commit_sha=None, credential_secret_id=None)
        )

    assert acquired.provenance.requested_ref is None
    assert acquired.provenance.subdirectory == ""


@pytest.mark.anyio
@pytest.mark.parametrize(
    "repository_url",
    [
        "http://github.com/owner/repo",
        "https://api.github.com/owner/repo",
        "https://user@github.com/owner/repo",
        "https://github.com:443/owner/repo",
        "https://github.com/owner/repo/issues",
        "https://github.com/owner/repo?token=secret",
        "https://github.com/owner/%72epo",
        "https://github.com/-owner/repo",
        " https://github.com/owner/repo",
        "https://github.com/owner/..",
    ],
)
async def test_invalid_repository_urls_fail_before_http(repository_url: str) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return json_response(request, {})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as captured:
            await GitHubSkillAcquirer(client).acquire(source(repository_url=repository_url))

    assert captured.value.code == "github_source_invalid"
    assert calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("ref", ["bad ref", "../main", "heads//main", "main.lock", "@", "refs/@{upstream}"])
async def test_invalid_refs_fail_before_http(ref: str) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: json_response(request, {}))) as client:
        with pytest.raises(GitHubAcquisitionError) as captured:
            await GitHubSkillAcquirer(client).acquire(source(ref=ref))
    assert captured.value.code == "github_source_invalid"


@pytest.mark.anyio
async def test_ref_is_encoded_as_one_path_parameter() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert b"heads%2Ffeature%2Fsafe" in request.url.raw_path
        return httpx2.Response(404, request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(GitHubAcquisitionError):
            await GitHubSkillAcquirer(client).acquire(source(ref="heads/feature/safe"))
    assert calls == 1


@pytest.mark.anyio
async def test_expected_commit_mismatch_stops_before_tree_reads() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return json_response(request, commit_payload())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as captured:
            await GitHubSkillAcquirer(client).acquire(source(expected_commit_sha="f" * 40))

    assert captured.value.code == "github_commit_mismatch"
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "headers", "expected_code"),
    [
        (401, {}, "github_auth_failed"),
        (403, {}, "github_auth_failed"),
        (403, {"x-ratelimit-remaining": "0", "retry-after": "12"}, "github_rate_limited"),
        (403, {"retry-after": "12"}, "github_rate_limited"),
        (404, {}, "github_auth_failed"),
        (429, {"retry-after": "12"}, "github_rate_limited"),
        (503, {}, "github_unavailable"),
    ],
)
async def test_provider_statuses_map_to_safe_stable_errors(
    status_code: int,
    headers: dict[str, str],
    expected_code: str,
) -> None:
    token = "private-token"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, headers=headers, text=f"provider leaked {token}", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as captured:
            await GitHubSkillAcquirer(client).acquire(source(), credential=token)

    assert captured.value.code == expected_code
    assert token not in str(captured.value)
    if expected_code == "github_rate_limited":
        assert captured.value.retry_after_seconds == 12


@pytest.mark.anyio
async def test_truncated_or_special_tree_entries_fail_before_blob_reads() -> None:
    cases = (
        ({"tree": [], "truncated": True}, "skill_package_limit"),
        (
            {
                "tree": [tree_entry("linked", entry_type="blob", mode="120000", sha="e" * 40, size=4)],
                "truncated": False,
            },
            "github_source_invalid",
        ),
        (
            {
                "tree": [tree_entry("submodule", entry_type="commit", mode="160000", sha="e" * 40)],
                "truncated": False,
            },
            "github_source_invalid",
        ),
    )
    for tree, expected_code in cases:
        calls = 0

        def handler(request: httpx2.Request, tree_payload: dict[str, object] = tree) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if request.url.path.endswith("/commits/main"):
                return json_response(request, commit_payload())
            return json_response(request, tree_payload)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises((GitHubAcquisitionError, SkillPackageError)) as captured:
                await GitHubSkillAcquirer(client).acquire(source(subdirectory=""))

        assert captured.value.code == expected_code
        assert calls == 2


@pytest.mark.anyio
async def test_tree_path_collisions_and_entry_limit_fail_before_blob_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_service.skills.github as github_module

    content_sha = git_blob_sha(b"x")
    collision = {
        "tree": [
            tree_entry("Readme.md", entry_type="blob", mode="100644", sha=content_sha, size=1),
            tree_entry("README.md", entry_type="blob", mode="100644", sha=content_sha, size=1),
        ],
        "truncated": False,
    }
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/commits/main"):
            return json_response(request, commit_payload())
        return json_response(request, collision)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as colliding:
            await GitHubSkillAcquirer(client).acquire(source(subdirectory=""))
    assert colliding.value.code == "github_source_invalid"
    assert calls == 2

    calls = 0
    monkeypatch.setattr(github_module, "MAX_GITHUB_TREE_ENTRIES", 1)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(SkillPackageError) as oversized:
            await GitHubSkillAcquirer(client).acquire(source(subdirectory=""))
    assert oversized.value.code == "skill_package_limit"
    assert calls == 2


@pytest.mark.anyio
async def test_blob_downloads_are_parallel_bounded_and_verified() -> None:
    files = {"SKILL.md": SKILL_DOCUMENT, "a.txt": b"a", "b.txt": b"b"}
    by_sha = {git_blob_sha(content): content for content in files.values()}
    active = 0
    max_active = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal active, max_active
        path = request.url.path
        if path.endswith("/commits/main"):
            return json_response(request, commit_payload())
        if path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return json_response(
                request,
                {
                    "tree": [
                        tree_entry(path, entry_type="blob", mode="100644", sha=git_blob_sha(content), size=len(content))
                        for path, content in files.items()
                    ],
                    "truncated": False,
                },
            )
        sha = path.rsplit("/", 1)[-1]
        active += 1
        max_active = max(max_active, active)
        await anyio.sleep(0.01)
        active -= 1
        return json_response(request, blob_payload(by_sha[sha]))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        acquired = await GitHubSkillAcquirer(client, blob_concurrency=2).acquire(source(subdirectory=""))

    assert acquired.package.manifest.skill_name == "deploy-helper"
    assert max_active == 2


@pytest.mark.anyio
async def test_blob_mismatch_and_acquisition_deadline_fail_as_unavailable() -> None:
    skill_sha = git_blob_sha(SKILL_DOCUMENT)

    async def mismatch_handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/commits/main"):
            return json_response(request, commit_payload())
        if path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return json_response(
                request,
                {
                    "tree": [
                        tree_entry(
                            "SKILL.md",
                            entry_type="blob",
                            mode="100644",
                            sha=skill_sha,
                            size=len(SKILL_DOCUMENT),
                        )
                    ],
                    "truncated": False,
                },
            )
        return json_response(request, blob_payload(b"different", sha=skill_sha, size=len(SKILL_DOCUMENT)))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(mismatch_handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as mismatch:
            await GitHubSkillAcquirer(client).acquire(source(subdirectory=""))
    assert mismatch.value.code == "github_unavailable"

    async def slow_handler(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(1)
        return json_response(request, commit_payload())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(slow_handler)) as client:
        with pytest.raises(GitHubAcquisitionError) as timed_out:
            await GitHubSkillAcquirer(client, deadline_seconds=0.01).acquire(source())
    assert timed_out.value.code == "github_unavailable"


@pytest.mark.anyio
async def test_transport_failure_and_invalid_json_are_unavailable() -> None:
    def transport_failure(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    def invalid_json(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not-json", request=request)

    handlers: tuple[Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]], ...] = (
        transport_failure,
        invalid_json,
    )
    for handler in handlers:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(GitHubAcquisitionError) as captured:
                await GitHubSkillAcquirer(client).acquire(source())
        assert captured.value.code == "github_unavailable"
        assert "down" not in str(captured.value)
        assert "not-json" not in str(captured.value)
