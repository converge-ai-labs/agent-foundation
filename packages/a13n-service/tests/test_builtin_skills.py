"""Builtin skill discovery and identity-based placement through real Service runs."""

import hashlib
import json
import posixpath
import re

import pytest
from a13n_service.resources.skills import builtin, package

from .mcp_support import call, client, key, rpc
from .test_skills import DOCUMENT, archive, create

pytestmark = pytest.mark.anyio
NAME = "platform-operations"


def test_portable_package_contains_release_schema_and_deployment_catalog():
    first = builtin.load_packages(b'{"plugins":["one"]}')[NAME]
    same = builtin.load_packages(b'{"plugins":["one"]}')[NAME]
    changed = builtin.load_packages(b'{"plugins":["two"]}')[NAME]
    assert first.digest == same.digest != changed.digest
    content = package.read_package(first.archive.content)
    assert json.loads(content.files["instance/catalog.json"]) == {"plugins": ["one"]}
    assert "builtin_skills" in json.loads(content.files["schemas/agent-config.json"])["properties"]
    for path, data in content.files.items():
        if not path.endswith(".md"):
            continue
        assert b"/platform-instance" not in data
        for target in re.findall(r"\]\(([^)]+)\)", data.decode()):
            assert posixpath.normpath(posixpath.join(posixpath.dirname(path), target.split("#", 1)[0])) in content.files
    assert b"../instance/catalog.json" in content.files["references/providers.md"]


async def test_catalog_is_separate_from_workspace_skills_and_admitted_to_mcp(service):
    response = await service.client.get(f"{service.api}/builtin-skills")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [service.runtime.builtin_skills[NAME].definition.model_dump()]}
    assert (await service.client.get(f"{service.api}/skills")).json()["items"] == []
    operation = service.app.openapi()["paths"]["/api/v1/builtin-skills"]["get"]
    assert operation["x-a13n-mcp"] is True
    denied = await service.client.get(f"{service.api}/builtin-skills", headers={"X-Workspace-ID": "ws_" + "0" * 24})
    assert denied.status_code in {403, 404}, denied.text


async def test_builtin_detail_and_download_match_the_current_package(service):
    response = await service.client.get(f"{service.api}/builtin-skills/{NAME}")
    assert response.status_code == 200, response.text
    detail = response.json()
    bundled = service.runtime.builtin_skills[NAME]
    assert detail == {
        **bundled.definition.model_dump(),
        "root": bundled.archive.root,
        "files": [{"path": path, "size": size} for path, size in bundled.archive.entries],
        "package_digest": bundled.digest,
        "package_size": len(bundled.archive.content),
    }
    for params in ({}, {"package_digest": detail["package_digest"]}):
        response = await service.client.get(f"{service.api}/builtin-skills/{NAME}/content", params=params)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert f"{NAME}.zip" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(response.content).hexdigest() == detail["package_digest"]
        assert len(response.content) == detail["package_size"]
        files = package.read_package(response.content).files
        assert {path: len(content) for path, content in files.items()} == {
            file["path"]: file["size"] for file in detail["files"]
        }
        assert b"# Platform operations" in files["SKILL.md"]
        assert "providers" in json.loads(files["instance/catalog.json"])
    changed = await service.client.get(
        f"{service.api}/builtin-skills/{NAME}/content", params={"package_digest": "0" * 64}
    )
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["details"]["reason"] == "package_changed"


@pytest.mark.parametrize("suffix", ["", "/content"])
async def test_builtin_reads_require_workspace_access_and_known_names(service, suffix):
    url = f"{service.api}/builtin-skills/{NAME}{suffix}"
    denied = await service.client.get(url, headers={"X-Workspace-ID": "ws_" + "0" * 24})
    assert denied.status_code in {403, 404}, denied.text
    unknown = await service.client.get(f"{service.api}/builtin-skills/unknown{suffix}")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["details"]["kind"] == "builtin_skill"
    service.client.cookies.clear()
    anonymous = await service.client.get(url)
    assert anonymous.status_code == 401, anonymous.text


async def test_builtin_detail_is_discoverable_and_callable_through_mcp(service):
    secret = await key(service)
    async with client(service, secret) as http:
        tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert "get_builtin_skill" in names
        assert "read_builtin_skill_archive" not in names
        result = await call(http, service, "GET", "/builtin-skills/{name}", name=NAME)
        assert result["status"] == 200
        assert result["body"]["name"] == NAME
        assert any(file["path"] == "SKILL.md" for file in result["body"]["files"])


@pytest.mark.parametrize("mode", ["shared", "isolated"])
async def test_custom_agent_materializes_builtin_and_workspace_skills_together(
    skill_service, scripted_model, runs_kit, mode
):
    service = skill_service
    await runs_kit.pause_sweeps(service)
    skill = await create(service, archive({"SKILL.md": DOCUMENT}), "review")
    config = {"mode": mode, **({"cleanup": True} if mode == "shared" else {})}
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        builtin_skills=[NAME],
        skills=[{"skill_id": skill["id"]}],
        skill_sync=config,
    )
    bundled = service.runtime.builtin_skills[NAME]
    directory = (
        f"/workspace/.agents/skills/{NAME}"
        if mode == "shared"
        else f"/workspace/.agents/skill-cache/{bundled.digest}/{NAME}"
    )
    scripted_model.call("view", {"file_path": f"{directory}/SKILL.md"}, call_id="guide")
    scripted_model.call(
        "view", {"file_path": f"{directory}/instance/catalog.json", "line_limit": 10}, call_id="catalog"
    )
    scripted_model.say("Read both files")
    submitted = await runs_kit.start_thread(service, agent, "Read the platform guide")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert run["status"] == "completed", run
    requests = [await scripted_model.request() for _ in range(3)]
    first = json.dumps(requests[0])
    assert first.count("<available-skills>") == 1
    assert NAME in first and "code-review" in first
    assert "/platform-skills" not in first and "/platform-instance" not in first
    assert "# Platform operations" in json.dumps(requests[-1])
    root = service.runtime.settings.provisioning.local.root / run["environment_mounts"][0]["environment_id"]
    relative = directory.removeprefix("/workspace/")
    assert (root / relative / "SKILL.md").exists()
    assert json.loads((root / relative / "instance/catalog.json").read_bytes())["providers"]
    assert len(list((root / ".agents").rglob("SKILL.md"))) == 2


async def test_custom_agent_without_primary_environment_fails_before_model_call(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, builtin_skills=[NAME])
    submitted = await runs_kit.start_thread(service, agent, "Read the guide")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert run["status"] == "failed", run
    assert run["failure"]["code"] == "invalid_argument"
    assert "primary environment" in run["failure"]["message"]
    assert scripted_model.requests.empty()


async def test_composer_keeps_host_mount_but_custom_copy_requires_primary(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    await runs_kit.create_model(service, scripted_model)
    prepared = await service.client.post(f"{service.api}/agent-composer")
    assert prepared.status_code == 200, prepared.text
    composer = prepared.json()
    scripted_model.call("view", {"file_path": f"/platform-skills/{NAME}/SKILL.md"}, call_id="guide")
    scripted_model.say("Read on Host")
    submitted = await runs_kit.start_thread(service, composer, "Read the guide")
    async with runs_kit.platform_runtime(service) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert run["status"] == "completed" and run["environment_mounts"] == [], (
        run,
        (await service.client.get(f"{service.api}/runs/{run['id']}/attempts")).json(),
    )
    assert "/platform-skills" in json.dumps(await scripted_model.request())
    assert "# Platform operations" in json.dumps(await scripted_model.request())
    copied = await service.client.post(f"{service.api}/agents/{composer['id']}/duplicate", json={"name": "My composer"})
    assert copied.status_code == 201, copied.text
    assert copied.json()["source"] == "custom"
    submitted = await runs_kit.start_thread(service, copied.json(), "Read the guide")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, submitted["run"]["id"])
    assert run["status"] == "failed", run
    assert "primary environment" in run["failure"]["message"]
    assert scripted_model.requests.empty()


async def test_workspace_skill_cannot_overwrite_selected_builtin_name(service, scripted_model, runs_kit):
    model = await runs_kit.create_model(service, scripted_model)
    skill = await create(service, archive({"SKILL.md": DOCUMENT.replace(b"code-review", NAME.encode())}), "collision")
    response = await service.client.post(
        f"{service.api}/agents",
        json={
            "name": "Conflicting skills",
            "config": {"model": model, "builtin_skills": [NAME], "skills": [{"skill_id": skill["id"]}]},
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"]["field"] == "skills.0"
