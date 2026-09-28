"""Skills: package validation, revisioned heads and their lists, content browsing, GitHub import, pins and
execution loading."""

import io
import os
import stat
import threading
import zipfile
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from a13n_service.infra import cursors
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.skills import content, package, pins
from a13n_service.resources.skills.github import GitHub
from a13n_service.resources.skills.schemas import (
    GitHubSource,
    Skill,
    SkillCreate,
    SkillPin,
    SkillRevisionCreate,
    UploadSource,
)
from a13n_service.resources.skills.service import create_revision, create_skill, validate_package
from a13n_service.runs import host
from a13n_service.runs import skills as run_skills
from a13n_service.runs.attempts import LeaseLost
from a13n_service.settings import LocalProvisioning, Provisioning, Settings
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import WorkspaceRow

pytestmark = pytest.mark.anyio

DOCUMENT = b"---\nname: code-review\ndescription: Review a change for correctness.\n---\n# Review\n"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


def archive(files: dict[str, bytes], *, links: tuple[str, ...] = ()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path, data in files.items():
            target.writestr(path, data)
        for path in links:
            info = zipfile.ZipInfo(path)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            target.writestr(info, "../../etc/passwd")
    return output.getvalue()


async def stage(service, data: bytes, request_key: str) -> str:  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/uploads",
        files={"file": ("skill.zip", data, "application/zip")},
        headers={"idempotency-key": request_key},
    )
    assert response.status_code == 200, response.text
    return response.json()["upload_id"]


async def create(service, data: bytes, request_key: str, **body: object) -> dict:  # type: ignore[no-untyped-def]
    upload_id = await stage(service, data, request_key)
    response = await service.client.post(
        f"{service.api}/skills", json={"source": {"kind": "upload", "upload_id": upload_id}, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_skill_revisions_and_package_content(service) -> None:  # type: ignore[no-untyped-def]
    # Packages zipped as a folder keep SKILL.md under one top-level directory.
    first = archive({"review/SKILL.md": DOCUMENT, "review/scripts/check.py": b"print('ok')\n"})
    skill = await create(service, first, "first", labels={"team": "platform"})
    assert skill["id"].startswith("sk_") and skill["name"] == "code-review" and "key" not in skill
    assert skill["description"] == "Review a change for correctness."
    base = f"{service.api}/skills"
    item = f"{base}/{skill['id']}"
    read = await service.client.get(item)
    assert read.json() == skill and read.headers["etag"] == f'"{skill["id"]}:{skill["version"]}"'

    revisions = (await service.client.get(f"{item}/revisions")).json()["items"]
    assert [revision["number"] for revision in revisions] == [1]
    first_revision = revisions[0]
    assert (first_revision["id"], first_revision["skill_id"]) == (skill["default_revision_id"], skill["id"])
    manifest = first_revision["config"]
    assert manifest["root"] == "review/" and manifest["source"]["kind"] == "upload"
    assert [file["path"] for file in manifest["files"]] == ["SKILL.md", "scripts/check.py"]
    revision_path = f"{item}/revisions/{first_revision['id']}"
    downloaded = await service.client.get(f"{revision_path}/content")
    assert downloaded.content == first
    assert downloaded.headers["content-disposition"] == "attachment; filename*=UTF-8''code-review-1.zip"
    assert downloaded.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert (await service.client.get(f"{revision_path}/files/scripts/check.py")).content == b"print('ok')\n"
    assert (await service.client.get(f"{revision_path}/files/missing.py")).status_code == 404

    second_upload = await stage(service, archive({"SKILL.md": DOCUMENT + b"More.\n"}), "second")
    body = {"source": {"kind": "upload", "upload_id": second_upload}, "note": "tighter", "make_default": False}
    assert (await service.client.post(f"{item}/revisions", json=body)).status_code == 428
    created = await service.client.post(f"{item}/revisions", json=body, headers={"if-match": etag(skill)})
    assert created.status_code == 201, created.text
    second = created.json()
    assert (second["number"], second["note"], second["config"]["root"]) == (2, "tighter", "")
    current = (await service.client.get(item)).json()
    # A new revision stamps the head even when its default stays.
    assert current["default_revision_id"] == first_revision["id"] and current["version"] == skill["version"] + 1
    # A package whose manifest equals the default revision's is that revision: nothing is created or stamped.
    same = {"source": first_revision["config"]["source"]}
    republished = await service.client.post(f"{item}/revisions", json=same, headers={"if-match": etag(current)})
    assert republished.status_code == 201 and republished.json()["id"] == first_revision["id"], republished.text
    assert (await service.client.get(item)).json()["version"] == current["version"]

    page = (await service.client.get(f"{item}/revisions", params={"limit": 1})).json()
    assert [revision["number"] for revision in page["items"]] == [2]
    rest = await service.client.get(f"{item}/revisions", params={"limit": 1, "cursor": page["next_cursor"]})
    assert [revision["number"] for revision in rest.json()["items"]] == [1]
    # A position past any revision number is refused before it reaches the database.
    beyond = cursors.encode("skill_revisions", skill["id"], 2**31)
    refused = await service.client.get(f"{item}/revisions", params={"cursor": beyond})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_cursor"

    promoted = await service.client.post(
        f"{item}/revisions/{second['id']}/set-default", headers={"if-match": etag(current)}
    )
    assert promoted.status_code == 200 and promoted.json()["default_revision_id"] == second["id"]
    unchanged = await service.client.post(
        f"{item}/revisions/{second['id']}/set-default", headers={"if-match": promoted.headers["etag"]}
    )
    assert unchanged.json()["version"] == promoted.json()["version"]

    renamed = await service.client.patch(
        item, json={"name": "Code review", "labels": {"team": "tools"}}, headers={"if-match": promoted.headers["etag"]}
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Code review"
    # A change to nothing keeps the version.
    same = await service.client.patch(item, json={"name": "Code review"}, headers={"if-match": renamed.headers["etag"]})
    assert same.status_code == 200 and same.json()["version"] == renamed.json()["version"]
    stale = await service.client.patch(item, json={"name": "x"}, headers={"if-match": promoted.headers["etag"]})
    assert stale.status_code == 412
    assert [s["id"] for s in (await service.client.get(base, params={"label": "team:tools"})).json()["items"]] == [
        skill["id"]
    ]
    assert (await service.client.get(base, params={"label": "team:platform"})).json()["items"] == []

    archived = await service.client.post(f"{item}/archive", headers={"if-match": renamed.headers["etag"]})
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    refused = await service.client.post(f"{item}/revisions", json=body, headers={"if-match": archived.headers["etag"]})
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "archived"
    # An archived skill changes only by unarchiving.
    closed = await service.client.patch(item, json={"name": "x"}, headers={"if-match": archived.headers["etag"]})
    assert closed.status_code == 409 and closed.json()["error"]["details"]["reason"] == "archived"
    # Archived revisions stay readable.
    assert (await service.client.get(f"{revision_path}/content")).status_code == 200
    restored = await service.client.post(f"{item}/unarchive", headers={"if-match": archived.headers["etag"]})
    assert restored.json()["archived_at"] is None

    packages = await content.load_packages(
        service.runtime.storage, service.runtime.objects, service.tenant.workspace_id, [first_revision["id"]]
    )
    assert packages[0].name == "code-review" and packages[0].digest == first_revision["digest"]
    assert dict(packages[0].files) == {"SKILL.md": DOCUMENT, "scripts/check.py": b"print('ok')\n"}
    with pytest.raises(ServiceError) as missing:
        await content.load_packages(
            service.runtime.storage, service.runtime.objects, service.tenant.workspace_id, ["skr_" + "0" * 24]
        )
    assert missing.value.code == "not_found"


async def test_unsafe_or_invalid_packages_are_refused(service) -> None:  # type: ignore[no-untyped-def]
    refused = [
        ({"SKILL.md": DOCUMENT, "../escape.txt": b"x"}, (), "stay inside the package"),
        ({"SKILL.md": DOCUMENT, "/etc/cron.d/x": b"x"}, (), "stay inside the package"),
        ({"SKILL.md": DOCUMENT, "a\\b.txt": b"x"}, (), "stay inside the package"),
        ({"SKILL.md": DOCUMENT}, ("link",), "only regular files"),
        ({"README.md": b"no skill"}, (), "SKILL.md must be at the archive root"),
        ({"a/SKILL.md": DOCUMENT, "b/other.md": b"x"}, (), "SKILL.md must be at the archive root"),
        ({"SKILL.md": b"# no frontmatter\n"}, (), "must begin with YAML frontmatter"),
        ({"SKILL.md": b"---\nname: x\n---\n"}, (), "valid name and description"),
        ({"SKILL.md": DOCUMENT, "Doc.md": b"a", "doc.md": b"b"}, (), "collide"),
    ]
    for index, (files, links, reason) in enumerate(refused):
        upload_id = await stage(service, archive(files, links=links), f"bad-{index}")
        response = await service.client.post(
            f"{service.api}/skills", json={"source": {"kind": "upload", "upload_id": upload_id}}
        )
        assert response.status_code == 400, response.text
        assert reason in response.json()["error"]["message"], files


async def test_package_limits_keys_and_authorization(service) -> None:  # type: ignore[no-untyped-def]
    crowded = {"SKILL.md": DOCUMENT, **{f"f/{index}.txt": b"" for index in range(1000)}}
    upload_id = await stage(service, archive(crowded), "crowded")
    too_many = await service.client.post(
        f"{service.api}/skills", json={"source": {"kind": "upload", "upload_id": upload_id}}
    )
    assert too_many.status_code == 413 and too_many.json()["error"]["details"] == {"limit": 1000}

    # Skills are identified by ID: two of a workspace may declare the same SKILL.md name, and a create names no key.
    source = {"kind": "upload", "upload_id": await stage(service, archive({"SKILL.md": DOCUMENT}), "named")}
    named = await service.client.post(f"{service.api}/skills", json={"source": source, "name": "Code Review"})
    assert named.status_code == 201 and named.json()["name"] == "Code Review", named.text
    again = await service.client.post(f"{service.api}/skills", json={"source": source})
    assert again.status_code == 201 and again.json()["id"] != named.json()["id"], again.text
    keyed = await service.client.post(f"{service.api}/skills", json={"source": source, "key": "review"})
    assert keyed.status_code == 400
    spaced = archive({"SKILL.md": b"---\nname: Code Review\ndescription: d\n---\n"})
    loose = {"kind": "upload", "upload_id": await stage(service, spaced, "spaced")}
    assert (await service.client.post(f"{service.api}/skills", json={"source": loose})).status_code == 201

    viewer = Principal(
        service.tenant.principal_id,
        "user",
        (Grant(service.tenant.organization_id, service.tenant.workspace_id, BUILT_IN_ROLES["viewer"]),),
    )
    runtime = service.runtime
    with pytest.raises(ServiceError) as denied:
        await create_skill(
            runtime.storage,
            runtime.objects,
            GitHub(runtime.endpoint_policy, timeout=5, max_bytes=1024),
            viewer,
            service.tenant.workspace_id,
            SkillCreate.model_validate({"source": source}),
        )
    assert denied.value.code == "forbidden"


@contextmanager
def github_server(repository: bytes, *, download: str | None = None) -> Iterator[tuple[str, list[str]]]:
    """A local stand-in for the GitHub commit, zipball redirect and archive download endpoints.

    The zipball redirects to this server unless `download` names another origin.
    """
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            if self.path.startswith("/repos/acme/skills/commits/"):
                found = self.path.endswith("/main")
                self.reply(200 if found else 422, COMMIT.encode() if found else b"{}")
            elif self.path == f"/repos/acme/skills/zipball/{COMMIT}":
                self.send_response(302)
                origin = download or f"http://127.0.0.1:{self.server.server_port}"
                self.send_header("location", f"{origin}/archive/{COMMIT}")
                self.send_header("content-length", "0")
                self.end_headers()
            elif self.path == f"/archive/{COMMIT}":
                self.reply(200, repository)
            else:
                self.reply(404, b"{}")

        def reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass  # the client stopped reading at its byte limit

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()


def admin(service) -> Principal:  # type: ignore[no-untyped-def]
    grant = Grant(service.tenant.organization_id, service.tenant.workspace_id, BUILT_IN_ROLES["admin"])
    return Principal(service.tenant.principal_id, "user", (grant,))


def importer(service, url: str, *, max_bytes: int = 1 << 24) -> GitHub:  # type: ignore[no-untyped-def]
    return GitHub(service.runtime.endpoint_policy, timeout=5, max_bytes=max_bytes, api_url=url)


async def import_skill(service, github: GitHub, source: dict) -> Skill:  # type: ignore[no-untyped-def]
    runtime, body = service.runtime, SkillCreate.model_validate({"source": source})
    return await create_skill(
        runtime.storage, runtime.objects, github, admin(service), service.tenant.workspace_id, body
    )


async def import_revision(service, github: GitHub, key: str, source: dict, *, if_match: str):  # type: ignore[no-untyped-def]
    runtime, body = service.runtime, SkillRevisionCreate.model_validate({"source": source})
    return await create_revision(
        runtime.storage,
        runtime.objects,
        github,
        admin(service),
        service.tenant.workspace_id,
        key,
        body,
        if_match=if_match,
    )


async def refused(call: Awaitable[object]) -> ServiceError:
    with pytest.raises(ServiceError) as error:
        await call
    return error.value


REPOSITORY = {
    "acme-skills-0123456/README.md": b"root",
    "acme-skills-0123456/skills/review/SKILL.md": DOCUMENT,
    "acme-skills-0123456/skills/review/notes.md": b"notes",
    "acme-skills-0123456/skills/other/SKILL.md": b"---\nname: other\ndescription: o\n---\n",
}
SOURCE = {"kind": "github", "repository": "acme/skills", "ref": "main", "path": "skills/review"}


async def test_github_import_records_the_resolved_commit(service) -> None:  # type: ignore[no-untyped-def]
    with github_server(archive(REPOSITORY)) as (url, requests):
        github = importer(service, url)
        skill = await import_skill(service, github, SOURCE)
        item = f"{service.api}/skills/{skill.id}"
        revision = (await service.client.get(f"{item}/revisions")).json()["items"][0]
        assert revision["config"]["source"] == {**SOURCE, "commit": COMMIT}
        assert [file["path"] for file in revision["config"]["files"]] == ["SKILL.md", "notes.md"]
        assert requests == [
            "/repos/acme/skills/commits/main",
            f"/repos/acme/skills/zipball/{COMMIT}",
            f"/archive/{COMMIT}",
        ]

        # Importing the same commit again reuses the staged package.
        again = await import_revision(service, github, skill.id, SOURCE, if_match=f'"{skill.id}:{skill.version}"')
        assert again.config.package_digest == revision["config"]["package_digest"]

        mismatch = await refused(import_skill(service, github, {**SOURCE, "commit": "f" * 40}))
        assert (mismatch.code, mismatch.details["reason"]) == ("conflict", "commit_mismatch")
        unknown = await refused(import_skill(service, github, {**SOURCE, "ref": "missing"}))
        empty = await refused(import_skill(service, github, {**SOURCE, "path": "skills/absent"}))
        assert unknown.code == empty.code == "invalid_argument"
    traversal = await service.client.post(
        f"{service.api}/skills", json={"source": {**SOURCE, "path": "skills/../secrets"}}
    )
    assert traversal.status_code == 400


async def test_github_imports_are_bounded_and_preconditions_come_first(service) -> None:  # type: ignore[no-untyped-def]
    # The archive host GitHub redirects to is checked like any other destination.
    with github_server(archive(REPOSITORY), download="http://169.254.169.254") as (url, requests):
        metadata = await refused(import_skill(service, importer(service, url), SOURCE))
    assert (metadata.code, metadata.details) == ("unavailable", {"dependency": "github"})
    assert requests == ["/repos/acme/skills/commits/main", f"/repos/acme/skills/zipball/{COMMIT}"]

    oversized = archive({**REPOSITORY, "acme-skills-0123456/skills/review/blob.bin": os.urandom(4096)})
    with github_server(oversized) as (url, requests):
        bounded = await refused(import_skill(service, importer(service, url, max_bytes=2048), SOURCE))
    assert (bounded.code, bounded.details) == ("payload_too_large", {"limit": 2048})

    # A stale ETag or an archived skill is refused before anything is fetched.
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "uploaded")
    with github_server(archive(REPOSITORY)) as (url, requests):
        github = importer(service, url)
        stale = await refused(import_revision(service, github, skill["id"], SOURCE, if_match=f'"{skill["id"]}:0"'))
        archived = await service.client.post(
            f"{service.api}/skills/{skill['id']}/archive", headers={"if-match": etag(skill)}
        )
        closed = await refused(import_revision(service, github, skill["id"], SOURCE, if_match=archived.headers["etag"]))
    assert (stale.code, closed.details["reason"]) == ("precondition_failed", "archived")
    assert requests == []


async def test_github_imports_spend_the_upload_budget(  # type: ignore[no-untyped-def]
    serve, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import stores the archive it reads as the caller's upload, so once uploads are spent it is refused before
    anything is read from GitHub."""
    fetched: list[GitHubSource] = []

    async def fetch(github: GitHub, source: GitHubSource) -> tuple[str, dict[str, bytes]]:
        fetched.append(source)
        raise AssertionError("an import past the upload budget read from GitHub")

    monkeypatch.setattr(GitHub, "fetch", fetch)
    objects = settings.objects.model_copy(update={"upload_limit": 1})
    async with serve(settings=settings.model_copy(update={"objects": objects})) as service:
        skill = await create(service, archive({"SKILL.md": DOCUMENT}), "only")
        skills = f"{service.api}/skills"
        for path in (skills, f"{skills}/validate", f"{skills}/{skill['id']}/revisions"):
            imported = await service.client.post(path, json={"source": SOURCE}, headers={"if-match": etag(skill)})
            assert imported.status_code == 429, (path, imported.text)
    assert fetched == []


async def test_package_validation_reads_the_manifest_and_stores_nothing(service, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    validate = f"{service.api}/skills/validate"
    source = {"kind": "upload", "upload_id": await stage(service, archive({"review/SKILL.md": DOCUMENT}), "checked")}
    checked = await service.client.post(validate, json={"source": source})
    assert checked.status_code == 200, checked.text
    manifest = checked.json()
    assert (manifest["name"], manifest["description"], manifest["root"]) == (
        "code-review",
        "Review a change for correctness.",
        "review/",
    )
    assert (await service.client.get(f"{service.api}/skills")).json()["items"] == []
    # Creation freezes exactly the manifest validation showed.
    skill = await service.client.post(f"{service.api}/skills", json={"source": source})
    revision = f"{service.api}/skills/{skill.json()['id']}/revisions/{skill.json()['default_revision_id']}"
    assert (await service.client.get(revision)).json()["config"] == manifest

    invalid = {"kind": "upload", "upload_id": await stage(service, archive({"README.md": b"x"}), "invalid")}
    rejected = await service.client.post(validate, json={"source": invalid})
    assert rejected.status_code == 400 and "SKILL.md must be at the archive root" in rejected.text
    unknown = await service.client.post(validate, json={"source": {**source, "upload_id": "upl_" + "0" * 64}})
    assert unknown.status_code == 404, unknown.text

    runtime, workspace_id = service.runtime, service.tenant.workspace_id
    viewer = Principal(
        service.tenant.principal_id,
        "user",
        (Grant(service.tenant.organization_id, workspace_id, BUILT_IN_ROLES["viewer"]),),
    )
    github = GitHub(runtime.endpoint_policy, timeout=5, max_bytes=1024)
    denied = await refused(
        validate_package(runtime.storage, runtime.objects, github, viewer, workspace_id, UploadSource(**source))
    )
    assert denied.code == "forbidden"

    # A GitHub package is read at the resolved commit and, unlike an import, never staged.
    uploads = settings.objects.root / f"orgs/{service.tenant.organization_id}/uploads"
    staged = sorted(uploads.iterdir())
    with github_server(archive(REPOSITORY)) as (url, requests):
        imported = await validate_package(
            runtime.storage,
            runtime.objects,
            importer(service, url),
            admin(service),
            workspace_id,
            GitHubSource.model_validate(SOURCE),
        )
    assert imported.source == GitHubSource.model_validate({**SOURCE, "commit": COMMIT})
    assert [file.path for file in imported.files] == ["SKILL.md", "notes.md"]
    assert len(requests) == 3 and sorted(uploads.iterdir()) == staged


async def test_skill_lists_filter_and_summarize_the_default_revision(service) -> None:  # type: ignore[no-untyped-def]
    review = await create(service, archive({"SKILL.md": DOCUMENT}), "review")
    lint = b"---\nname: lint-all\ndescription: Report 100% of style issues in snake_case.\n---\n"
    linter = await create(service, archive({"SKILL.md": lint}), "lint")
    with github_server(archive(REPOSITORY)) as (url, _):
        other = await import_skill(service, importer(service, url), {**SOURCE, "path": "skills/other"})
    base = f"{service.api}/skills"

    async def names(**params: str) -> set[str]:
        response = await service.client.get(base, params=params)
        assert response.status_code == 200, response.text
        return {item["name"] for item in response.json()["items"]}

    # Text matches the name or description, ignoring case; wildcards are literal.
    assert await names(q="REVIEW") == {"code-review"}
    assert await names(q="style") == await names(q="%") == await names(q="_") == {"lint-all"}
    assert await names(source="github") == {"other"}
    assert await names(source="upload") == {"code-review", "lint-all"}
    await service.client.post(f"{base}/{linter['id']}/archive", headers={"if-match": etag(linter)})
    assert await names(archived="true") == {"lint-all"}
    assert await names(archived="false") == {"code-review", "other"}
    assert await names() == {"code-review", "lint-all", "other"}
    for params in ({"source": "zip"}, {"q": ""}, {"q": "x" * 257}):
        assert (await service.client.get(base, params=params)).status_code == 400, params

    [listed] = [item for item in (await service.client.get(base)).json()["items"] if item["id"] == other.id]
    assert listed["default_revision"] == {
        "id": other.default_revision_id,
        "number": 1,
        "source": {**SOURCE, "path": "skills/other", "commit": COMMIT},
    }
    upload_id = await stage(service, archive({"SKILL.md": DOCUMENT + b"More.\n"}), "second")
    item = f"{base}/{review['id']}"
    second = await service.client.post(
        f"{item}/revisions",
        json={"source": {"kind": "upload", "upload_id": upload_id}},
        headers={"if-match": etag(review)},
    )
    assert second.status_code == 201, second.text
    current = (await service.client.get(item)).json()
    assert current["default_revision"] == {
        "id": second.json()["id"],
        "number": 2,
        "source": {"kind": "upload", "upload_id": upload_id},
    }


async def test_a_revision_may_declare_another_name(service) -> None:  # type: ignore[no-untyped-def]
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "first")
    renamed = archive({"SKILL.md": b"---\nname: review\ndescription: Review.\n---\n"})
    source = {"kind": "upload", "upload_id": await stage(service, renamed, "renamed")}
    item = f"{service.api}/skills/{skill['id']}"
    revision = await service.client.post(
        f"{item}/revisions", json={"source": source}, headers={"if-match": etag(skill)}
    )
    assert revision.status_code == 201, revision.text
    assert (revision.json()["skill_id"], revision.json()["config"]["name"]) == (skill["id"], "review")


async def test_a_skill_is_found_only_in_its_workspace(service) -> None:  # type: ignore[no-untyped-def]
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "mine")
    second = new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(WorkspaceRow(id=second, organization_id=service.tenant.organization_id, name="Second"))
    in_second, item = {"x-workspace-id": second}, f"{service.api}/skills/{skill['id']}"
    assert (await service.client.get(item, headers=in_second)).status_code == 404
    moved = await service.client.patch(item, json={"name": "Moved"}, headers={"if-match": etag(skill), **in_second})
    assert moved.status_code == 404
    assert (await service.client.get(item)).json()["name"] == skill["name"]


def with_declared(files: dict[str, bytes], change: str) -> bytes:
    """An archive whose last member's central directory entry is altered after its data was written."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path, data in files.items():
            target.writestr(path, data)
        member = target.infolist()[-1]
        if change == "encrypted":
            member.flag_bits |= 0x1
        else:
            member.file_size += 1 if change == "larger" else -1
    return output.getvalue()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("larger", "does not match its declared size"),
        ("smaller", "not a readable zip"),
        ("encrypted", "encrypted archive members"),
    ],
)
def test_archive_members_must_match_their_declared_entries(change: str, reason: str) -> None:
    with pytest.raises(ServiceError) as error:
        package.read_package(with_declared({"SKILL.md": DOCUMENT, "notes.md": b"n" * 64}, change))
    assert error.value.code == "invalid_argument" and reason in error.value.message


@pytest.mark.parametrize(
    ("files", "limit"),
    [
        ({"big.bin": bytes(package.MAX_FILE_BYTES + 1)}, package.MAX_FILE_BYTES),
        # With SKILL.md, four members at the per-file limit exceed the expanded limit.
        ({f"part-{index}.bin": bytes(package.MAX_FILE_BYTES) for index in range(4)}, package.MAX_EXPANDED_BYTES),
    ],
    ids=["file", "expanded"],
)
def test_archive_expansion_is_bounded(files: dict[str, bytes], limit: int) -> None:
    with pytest.raises(ServiceError) as error:
        package.read_package(archive({"SKILL.md": DOCUMENT, **files}))
    assert (error.value.code, error.value.details) == ("payload_too_large", {"limit": limit})


async def test_pins_and_the_agents_pinning_a_skill(service) -> None:  # type: ignore[no-untyped-def]
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "pinned")
    revision_id = skill["default_revision_id"]
    workspace_id, storage = service.tenant.workspace_id, service.runtime.storage
    agent_id, agent_revision_id = new_object_id("ap"), new_object_id("apr")
    async with transaction(storage) as session:
        session.add(
            AgentRow(
                id=agent_id,
                organization_id=service.tenant.organization_id,
                workspace_id=workspace_id,
                name="Reviewer",
                description="",
                labels={},
                default_revision_id=agent_revision_id,
                created_by_id=service.tenant.principal_id,
                updated_by_id=service.tenant.principal_id,
            )
        )
        await session.flush()
        session.add(
            AgentRevisionRow(
                id=agent_revision_id,
                organization_id=service.tenant.organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                number=1,
                config={"skills": [{"skill_id": skill["id"], "revision_id": revision_id}]},
                digest="0" * 64,
                created_by_id=service.tenant.principal_id,
            )
        )

    # The agents listing answers which agents pin a skill or one revision of it.
    agents = f"{service.api}/agents"

    async def pinning(**params: str) -> list[str]:
        response = await service.client.get(agents, params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert await pinning(skill_id=skill["id"]) == [agent_id]
    assert await pinning(skill_id=skill["id"], skill_revision_id=revision_id) == [agent_id]
    assert await pinning(skill_revision_id="skr_" + "0" * 24) == []
    assert await pinning(skill_id=new_object_id("sk")) == []

    # A new pin is refused at its field path.
    pin = SkillPin(skill_id=skill["id"], revision_id=revision_id)
    async with short_session(storage) as session:
        await pins.require_pins(session, workspace_id, {"skills.0": pin})
        with pytest.raises(ServiceError) as foreign:
            await pins.require_pins(
                session, workspace_id, {"skills.1": SkillPin(skill_id=new_object_id("sk"), revision_id=revision_id)}
            )
    assert (foreign.value.details["field"], foreign.value.details["kind"]) == ("skills.1", "skill_revision")
    await service.client.post(f"{service.api}/skills/{skill['id']}/archive", headers={"if-match": etag(skill)})
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as archived:
            await pins.require_pins(session, workspace_id, {"skills.0": pin})
    assert archived.value.details["reason"] == "archived"


async def _skilled_run(service, runs_kit, scripted_model, tmp_path) -> tuple[dict, Path]:  # type: ignore[no-untyped-def]
    """A run of an agent with a pinned skill in a local primary environment, and where its skills materialize."""
    await runs_kit.pause_sweeps(service)
    provider = await service.client.post(
        f"{service.api}/environment-providers", json={"type": "local", "name": "Local"}
    )
    assert provider.status_code == 201, provider.text
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Local",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"root": {"path": str(tmp_path)}}},
        },
    )
    assert template.status_code == 201, template.text
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "materialized")
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        skills=[{"skill_id": skill["id"]}],
        default_environment_template_id=template.json()["id"],
    )
    run = (await runs_kit.start_thread(service, agent, "review it"))["run"]
    [mount] = run["environment_mounts"]
    return run, tmp_path / mount["environment_id"] / ".a13n" / "skills"


def _with_local_environments(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "provisioning": Provisioning(
                local=LocalProvisioning(enabled=True, root=settings.objects.root.parent / "environments")
            )
        }
    )


async def test_a_skill_package_the_object_store_cannot_serve_is_materialized_by_a_later_attempt(
    serve, settings: Settings, scripted_model, runs_kit, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """An object store outage while a run materializes its pinned skills ends the attempt, and a later one
    retries."""
    async with serve(settings=_with_local_environments(settings)) as service:
        run, skills_root = await _skilled_run(service, runs_kit, scripted_model, tmp_path)

        async def unavailable(*args: object, **kwargs: object) -> None:
            raise ServiceError("unavailable", "The object store is unavailable", {"dependency": "objects"})

        monkeypatch.setattr(run_skills, "load_packages", unavailable)
        await (await runs_kit.attempt(service))
        released = await runs_kit.get_run(service, run["id"])
        assert released["status"] == "accepted", released
        attempts = (await service.client.get(f"{service.api}/runs/{run['id']}/attempts")).json()["items"]
        assert [(item["status"], item["failure"]["code"]) for item in attempts] == [("failed", "attempt_failed")]
        assert not list(skills_root.glob("*.complete")) and scripted_model.requests.empty()

        monkeypatch.undo()
        scripted_model.say("Reviewed")
        await (await runs_kit.attempt(service))
        completed = await runs_kit.get_run(service, run["id"])
        assert (completed["status"], completed["attempts"]) == ("completed", 2), completed
        [document] = skills_root.glob("*/SKILL.md")
        assert document.read_bytes() == DOCUMENT and (skills_root / f"{document.parent.name}.complete").exists()


async def test_a_lease_lost_while_skills_materialize_ends_the_attempt_unsealed(
    serve, settings: Settings, scripted_model, runs_kit, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """The Harness wraps what a skill materializer raises; a lost lease still ends the attempt without a seal."""
    async with serve(settings=_with_local_environments(settings)) as service:
        run, skills_root = await _skilled_run(service, runs_kit, scripted_model, tmp_path)

        async def lost(*args: object, **kwargs: object) -> None:
            raise LeaseLost()

        monkeypatch.setattr(host, "prove", lost)
        with pytest.raises(LeaseLost):
            await (await runs_kit.attempt(service))
        running = await runs_kit.get_run(service, run["id"])
        assert running["status"] == "running", running
        assert not list(skills_root.glob("*.complete")) and scripted_model.requests.empty()
