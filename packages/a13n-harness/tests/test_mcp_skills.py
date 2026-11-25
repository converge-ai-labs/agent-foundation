"""Exercise the wire extension, catalog freeze, and model-facing read boundary."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from hashlib import sha256

import httpx2
import pytest
from a13n_harness import AgentSpec, DefinitionError, HarnessBuilder, HarnessExtensionEvent, RunBindings
from a13n_harness.capabilities import SkillCatalogResolvedEvent, SkillManager, SkillsCapability, SkillsPolicy
from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.mcp import InitializationRetryClient, MCPServer
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.extensions import MethodBinding, ServerExtension, read_client_extension_settings
from fastmcp.skills import ListSkillsResult, Skill, SkillsClientExtension
from fastmcp.skills.models import ListSkillsParams
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .environment_helpers import DirectLocalSource

pytestmark = pytest.mark.anyio

_DOC = "---\nname: remote\ndescription: Remote workflow\n---\nRead references/guide.md first."
_GUIDE = "Supporting instructions."
_ROOT = "skill://remote/"


def _binding(root, *, deny=()):
    mount = EnvironmentMount(
        source=DirectLocalSource(
            DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=root)),
            environment_id="mcp-skills",
        ),
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction) - set(deny)),
        working_directory="/",
    )
    return create_environment_runtime(mounts={"local": mount}, default_mount="local")


def _resource(uri, text):
    data = text.encode()
    return {"uri": uri, "digest": f"sha256:{sha256(data).hexdigest()}", "size": len(data)}


def _entry(*, document=_DOC, name="remote", root=_ROOT, extra=(), guide=_GUIDE):
    return Skill.model_validate(
        {
            "uri": root + "SKILL.md",
            "frontmatter": {"name": name, "description": "Remote workflow"},
            "resources": [
                _resource(root + "SKILL.md", document),
                _resource(root + "references/guide.md", guide),
                *extra,
            ],
        }
    )


class _TestSkillsExtension(ServerExtension):
    identifier = SkillsClientExtension.identifier

    def __init__(self, pages):
        self.pages = pages

    def methods(self):
        return (MethodBinding("skills/list", ListSkillsParams, self._list),)

    async def _list(self, context, params):
        assert read_client_extension_settings(context, self.identifier) == {}
        index = int(params.cursor) if params.cursor else 0
        return ListSkillsResult(
            skills=self.pages[index], nextCursor=str(index + 1) if index + 1 < len(self.pages) else None
        )


@asynccontextmanager
async def _server(
    *,
    pages=None,
    advertise=True,
    mode="auto",
    on_error="raise",
    document=_DOC,
    guide=_GUIDE,
    fail_list=False,
    wrong_uri=False,
    input_required=False,
    wrap_toolset=False,
):
    server = FastMCP("skills fixture")
    calls = []
    openings = []

    @server.tool
    def ping() -> str:
        return "pong"

    @server.resource(_ROOT + "SKILL.md")
    def skill_md() -> str:
        return document

    @server.resource(_ROOT + "references/guide.md")
    def supporting_file() -> str:
        return guide

    @server.resource(_ROOT + "secret.md")
    def unrelated() -> str:
        raise AssertionError("unlisted resources must never be requested")

    if advertise:
        server.add_extension(_TestSkillsExtension(pages if pages is not None else [[_entry()]]))
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    upstream = httpx2.ASGITransport(app=app)

    async def respond(request):
        if request.method == "POST":
            body = json.loads(request.content)
            method, params = body["method"], body.get("params", {})
            calls.append((method, params.get("uri", params.get("cursor"))))
            if method == "skills/list" and fail_list:
                return httpx2.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32603, "message": "unavailable"}}
                )
            if method == "resources/read" and (wrong_uri or input_required):
                return httpx2.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {"resultType": "input_required", "requestState": "interactive"}
                        if input_required
                        else {
                            "resultType": "complete",
                            "contents": [{"uri": _ROOT + "secret.md", "text": "wrong source"}],
                        },
                    },
                )
        return await upstream.handle_async_request(request)

    def http_client(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), **kwargs)

    async def prepare(ctx):
        async def create_toolset():
            client = InitializationRetryClient(
                StreamableHttpTransport("http://fixture/mcp", httpx_client_factory=http_client),
                mode=mode,
                extensions=[SkillsClientExtension()],
            )
            openings.append(client)
            toolset = MCPToolset(client, id="fixture")
            return toolset.filtered(lambda ctx, definition: True) if wrap_toolset else toolset

        return create_toolset

    async with app.router.lifespan_context(app):
        yield MCPServer(id="fixture", prepare=prepare, on_error=on_error), calls, openings
    assert all(not client.is_connected() for client in openings)


async def _run(model, server, *, manager=None, bindings=None, enable=True, extra=()):
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(SkillsCapability(manager, mcp_servers=(server, *extra) if enable else ()), server),
    )
    result = await executable.run("Use skills", bindings=bindings or RunBindings.embedded())
    assert result.output_or_raise() == "done"


def _delta(tool_name, **arguments):
    return {0: DeltaToolCall(name=tool_name, json_args=json.dumps(arguments))}


def _parts(messages, kind):
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, kind)
    ]


@pytest.mark.parametrize("wrap_toolset", [False, True])
async def test_mcp_skills_freeze_metadata_and_read_only_on_demand(tmp_path, wrap_toolset):
    observed = []
    turn = 0
    async with _server(
        pages=[[_entry()], [_entry(name="unused", root="skill://unused/")]], wrap_toolset=wrap_toolset
    ) as (server, calls, clients):

        async def model(messages, info):
            nonlocal turn
            turn += 1
            reads = [uri for method, uri in calls if method == "resources/read"]
            if turn == 1:
                assert not reads
                assert [(method, cursor) for method, cursor in calls if method == "skills/list"] == [
                    ("skills/list", None),
                    ("skills/list", "1"),
                ]
                assert len(clients) == 1
                (catalog,) = [
                    event.event.catalog for event in observed if isinstance(event.event, SkillCatalogResolvedEvent)
                ]
                assert [(item.name, item.source_id, item.uri) for item in catalog.mcp_items] == [
                    ("remote", "mcp:fixture", _ROOT + "SKILL.md"),
                    ("unused", "mcp:fixture", "skill://unused/SKILL.md"),
                ]
                assert not catalog.items
                assert "mcp:fixture" not in info.instructions
                assert "Remote workflow" in info.instructions
                assert _DOC not in info.instructions
                assert _ROOT not in info.instructions
                assert (
                    '<skill name="remote">\n  <description>Remote workflow</description>\n</skill>' in info.instructions
                )
                tool = next(tool for tool in info.function_tools if tool.name == "read_skill")
                assert set(tool.parameters_json_schema["properties"]) == {"name", "path"}
                yield _delta("read_skill", name="remote")
            elif turn == 2:
                assert reads == [_ROOT + "SKILL.md"]
                assert _parts(messages, ToolReturnPart)[-1].content == {
                    "source_id": "mcp:fixture",
                    "name": "remote",
                    "path": "SKILL.md",
                    "content": _DOC,
                    "files": ["SKILL.md", "references/guide.md"],
                }
                yield _delta("read_skill", name="remote", path="references/guide.md")
            elif turn == 3:
                assert reads == [_ROOT + "SKILL.md", _ROOT + "references/guide.md"]
                assert _parts(messages, ToolReturnPart)[-1].content == {
                    "source_id": "mcp:fixture",
                    "name": "remote",
                    "path": "references/guide.md",
                    "content": _GUIDE,
                }
                yield _delta("ping")
            else:
                assert _parts(messages, ToolReturnPart)[-1].content == "pong"
                yield "done"

        await _run(
            model,
            server,
            bindings=RunBindings.embedded(environment=_binding(tmp_path), producer_observer=observed.append),
        )
    assert not list(tmp_path.iterdir())
    assert len([event for event in observed if isinstance(event.event, SkillCatalogResolvedEvent)]) == 1
    (access,) = [
        event.event.payload
        for event in observed
        if isinstance(event.event, HarnessExtensionEvent) and event.event.payload.get("type") == "skill_accessed"
    ]
    assert access["source_id"] == "mcp:fixture"
    assert access["path"] == _ROOT + "SKILL.md"
    assert access["tool_id"] == "read_skill"


@pytest.mark.parametrize(
    ("mode", "advertise", "enable", "fail_list"),
    [
        ("legacy", True, True, False),
        ("auto", False, True, False),
        ("auto", True, False, False),
        ("auto", True, True, True),
    ],
)
async def test_mcp_skills_downgrade_preserves_tools(mode, advertise, enable, fail_list):
    async with _server(
        mode=mode, advertise=advertise, fail_list=fail_list, on_error="skip" if fail_list else "raise"
    ) as (server, calls, clients):

        async def model(messages, info):
            assert {tool.name for tool in info.function_tools} == {"ping"}
            assert "Remote workflow" not in (info.instructions or "")
            if not _parts(messages, ToolReturnPart):
                yield _delta("ping")
            else:
                assert _parts(messages, ToolReturnPart)[-1].content == "pong"
                yield "done"

        await _run(model, server, enable=enable)
        assert len(clients) == 1
        assert sum(method == "skills/list" for method, _ in calls) == int(fail_list)


async def test_mcp_skills_skip_unavailable_server_without_retrying_startup():
    attempts = 0

    async def prepare(ctx):
        async def create_toolset():
            nonlocal attempts
            attempts += 1
            raise ConnectionError("offline fixture")

        return create_toolset

    unavailable = MCPServer(id="unavailable", prepare=prepare, on_error="skip")
    async with _server() as (server, calls, _):

        async def model(messages, info):
            assert "Remote workflow" in info.instructions
            assert "mcp:unavailable" not in info.instructions
            yield "done"

        await _run(model, unavailable, extra=(server,))
    assert attempts == 1
    assert ("skills/list", None) in calls


@pytest.mark.parametrize("conflict", [None, "error", "prefer_earlier", "prefer_later"])
async def test_mcp_skills_follow_workspace_conflict_policy(tmp_path, conflict):
    path = tmp_path / ".agents" / "skills" / "remote"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("---\nname: remote\ndescription: Local workflow\n---\n")
    async with _server() as (server, calls, _):

        async def model(messages, info):
            assert conflict != "error"
            assert ("Local workflow" in info.instructions) == (conflict == "prefer_earlier")
            assert ("Remote workflow" in info.instructions) == (conflict == "prefer_later")
            assert "read_skill" in {tool.name for tool in info.function_tools}
            returns = _parts(messages, ToolReturnPart)
            if not returns:
                yield _delta("read_skill", name="remote")
            else:
                read = returns[-1].content
                winner = "workspace" if conflict == "prefer_earlier" else "mcp:fixture"
                assert read["source_id"] == winner
                assert ("files" in read) == (conflict == "prefer_later")
                yield "done"

        async def run():
            return await _run(
                model,
                server,
                manager=SkillManager.default(
                    policy=SkillsPolicy() if conflict is None else SkillsPolicy(conflict=conflict)
                ),
                bindings=RunBindings.embedded(environment=_binding(tmp_path)),
            )

        if conflict in {None, "error"}:
            with pytest.raises(DefinitionError) as exc:
                await run()
            assert exc.value.code == "skill_catalog_ambiguous"
        else:
            await run()
        assert sum(method == "resources/read" for method, _ in calls) == int(conflict == "prefer_later")


@pytest.mark.parametrize("workspace_skill", [False, True])
async def test_mcp_skills_enforce_source_and_merged_catalog_limits(tmp_path, workspace_skill):
    if workspace_skill:
        path = tmp_path / ".agents" / "skills" / "local"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("---\nname: local\ndescription: Local workflow\n---\n")
    pages = [[_entry()]] if workspace_skill else [[_entry()], [_entry(name="extra", root="skill://extra/")], []]
    async with _server(pages=pages) as (server, calls, _):

        async def model(messages, info):
            pytest.fail("Oversized catalog reached the model")
            yield "unused"

        with pytest.raises(DefinitionError) as exc:
            await _run(
                model,
                server,
                manager=SkillManager.default(policy=SkillsPolicy(max_skills=1)),
                bindings=RunBindings.embedded(environment=_binding(tmp_path)),
            )
        assert exc.value.code == "skill_catalog_too_large"
    assert [key for method, key in calls if method == "skills/list"] == ([None] if workspace_skill else [None, "1"])


@pytest.mark.parametrize(
    "path", ["../secret.md", "%2e%2e/secret.md", "/secret.md", "references\\guide.md", _ROOT + "secret.md", "secret.md"]
)
async def test_mcp_skills_reject_unlisted_or_unsafe_paths_before_rpc(path):
    async with _server() as (server, calls, _):

        async def model(messages, info):
            if not _parts(messages, ToolReturnPart):
                yield _delta("read_skill", name="remote", path=path)
            else:
                assert "relative file path" in _parts(messages, ToolReturnPart)[-1].content["error"]
                yield "done"

        await _run(model, server)
        assert not any(method == "resources/read" for method, _ in calls)


@pytest.mark.parametrize(
    "case", ["declared size", "actual size", "digest", "frontmatter", "response uri", "interactive read"]
)
async def test_mcp_skill_read_rejects_unverified_contents(case):
    altered = _DOC.replace("Remote workflow", "Different workflow")
    path = "SKILL.md" if case in {"frontmatter", "response uri"} else "references/guide.md"
    entry = _entry(
        document=altered if case == "frontmatter" else _DOC, guide="x" * 200 if case == "declared size" else _GUIDE
    )
    expected = {
        "declared size": "byte limit",
        "actual size": "byte limit",
        "digest": "frozen manifest",
        "frontmatter": "frontmatter differs",
        "response uri": "requested skill file",
        "interactive read": "unavailable",
    }[case]
    async with _server(
        pages=[[entry]],
        document=altered if case == "frontmatter" else _DOC,
        guide="é" * 65 if case == "actual size" else "unexpected content!" if case == "digest" else _GUIDE,
        wrong_uri=case == "response uri",
        input_required=case == "interactive read",
    ) as (server, calls, _):

        async def model(messages, info):
            if not _parts(messages, ToolReturnPart):
                yield _delta("read_skill", name="remote", path=path)
            else:
                assert expected in _parts(messages, ToolReturnPart)[-1].content["error"]
                yield "done"

        await _run(model, server, manager=SkillManager.default(policy=SkillsPolicy(max_file_bytes=128)))
    assert sum(method == "resources/read" for method, _ in calls) == int(case != "declared size")


@pytest.mark.parametrize(
    "uri", [_ROOT + "guide.md?elsewhere", _ROOT + "guide.md#fragment", _ROOT + "%252e%252e/secret.md"]
)
async def test_mcp_skills_reject_manifest_uri_aliases(uri):
    async with _server(pages=[[_entry(extra=[_resource(uri, _GUIDE)])]]) as (server, calls, _):

        async def model(messages, info):
            assert {tool.name for tool in info.function_tools} == {"ping"}
            assert "Remote workflow" not in (info.instructions or "")
            yield "done"

        await _run(model, server)
    assert not any(method == "resources/read" for method, _ in calls)


async def test_host_selection_fences_remote_reads_by_name():
    async with _server(pages=[[_entry(), _entry(name="unused", root="skill://unused/")]]) as (server, calls, _):

        async def model(messages, info):
            assert '<skill name="unused">' not in info.instructions
            returns = _parts(messages, ToolReturnPart)
            if not returns:
                yield _delta("read_skill", name="unused")
            else:
                assert "selected skill" in returns[-1].content["error"]
                yield "done"

        await _run(model, server, bindings=RunBindings.embedded(skill_selection=frozenset({"remote"})))
    assert not any(method == "resources/read" for method, _ in calls)


async def test_read_skill_local_only_reads_bounded_files_without_a_file_toolset(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "remote"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_DOC.encode())
    (skill / "references" / "guide.md").write_bytes(_GUIDE.encode())
    (skill / "large.md").write_bytes(("é" * 65).encode())
    (tmp_path / "outside.md").write_text("outside the skill")
    requests = [
        ({}, {"source_id": "workspace", "name": "remote", "path": "SKILL.md", "content": _DOC}),
        (
            {"path": "references/guide.md"},
            {"source_id": "workspace", "name": "remote", "path": "references/guide.md", "content": _GUIDE},
        ),
        ({"path": "../../../outside.md"}, "relative file path"),
        ({"path": "large.md"}, "environment_too_large"),
    ]
    observed = []

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == {"read_skill"}
        assert "<path>/workspace/.agents/skills/remote</path>" in info.instructions
        returns = _parts(messages, ToolReturnPart)
        if returns:
            expected = requests[len(returns) - 1][1]
            actual = returns[-1].content
            if isinstance(expected, str):
                assert expected in actual["error"]
            else:
                assert actual == expected
        if len(returns) < len(requests):
            yield _delta("read_skill", name="remote", **requests[len(returns)][0])
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(SkillsCapability(SkillManager.default(policy=SkillsPolicy(max_file_bytes=128))),),
    )
    result = await executable.run(
        "Use skills", bindings=RunBindings.embedded(environment=_binding(tmp_path), producer_observer=observed.append)
    )
    assert result.output_or_raise() == "done"
    (access,) = [
        event.event.payload
        for event in observed
        if isinstance(event.event, HarnessExtensionEvent) and event.event.payload.get("type") == "skill_accessed"
    ]
    assert access == {
        "type": "skill_accessed",
        "skill_name": "remote",
        "source_id": "workspace",
        "path": "/workspace/.agents/skills/remote",
        "tool_id": "read_skill",
    }


@pytest.mark.parametrize("restriction", ["symlink", "read permission"])
async def test_read_skill_local_respects_provider_path_and_read_authority(tmp_path, restriction):
    skill = tmp_path / ".agents" / "skills" / "remote"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_DOC.encode())
    if restriction == "symlink":
        other = tmp_path / "other"
        other.mkdir()
        (other / "reference.md").write_text("outside the skill")
        try:
            (skill / "references").symlink_to(other, target_is_directory=True)
        except OSError:
            pytest.skip("This host cannot create symlinks")
        path, deny = "references/reference.md", ()
    else:
        path, deny = "SKILL.md", (EnvironmentAction.FILE_READ_BYTES,)

    async def model(messages, info):
        assert 'name="remote"' in info.instructions
        returns = _parts(messages, ToolReturnPart)
        if not returns:
            yield _delta("read_skill", name="remote", path=path)
        else:
            assert "environment_denied" in returns[-1].content["error"]
            assert "content" not in returns[-1].content
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(SkillsCapability(),),
    )
    result = await executable.run(
        "Use skills", bindings=RunBindings.embedded(environment=_binding(tmp_path, deny=deny))
    )
    assert result.output_or_raise() == "done"
