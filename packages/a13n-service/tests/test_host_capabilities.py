"""Host capabilities a run's Harness opens: web providers and transport, skills, secrets and asset publication.

Each capability runs through the real Harness over a development `local` environment, with a function model
scripting the tool calls a run's model would make.
"""

import io
import json
import zipfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    AgentSpec,
    EnvironmentMount,
    HarnessBuilder,
    HarnessRunResult,
    RunBindings,
    RunError,
)
from a13n_harness.capabilities.web import WebCapability, WebSearchRequest, WebSearchResponse, WebSearchResult
from a13n_harness.errors import DefinitionError
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.web.definition import WebProviderDefinition
from a13n_harness.providers.web.options import SearchOptions
from a13n_harness.providers.web.transport import WebProviderTransport
from a13n_harness.tools import current_invocation_scope
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_service.distribution import OSS
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.environments.local import LocalEnvironment
from a13n_service.providers.registry import Registry
from a13n_service.resources.agents.schemas import SkillSelection
from a13n_service.resources.agents.toolsets import FetchConfiguration, SearchConfiguration, WebTools, web_configuration
from a13n_service.resources.assets.service import get_asset, read_asset_content
from a13n_service.resources.providers.schemas import ProviderCreate
from a13n_service.resources.providers.service import create_provider
from a13n_service.resources.providers.tables import WebProviderRow
from a13n_service.resources.secrets.schemas import SecretCreate, SecretRequirement
from a13n_service.resources.secrets.service import create_secret
from a13n_service.resources.skills.github import GitHub
from a13n_service.resources.skills.schemas import SkillCreate, UploadSource
from a13n_service.resources.skills.service import create_skill
from a13n_service.resources.uploads.service import stage
from a13n_service.runs.admission import CallContext
from a13n_service.runs.assets import AssetsCapability
from a13n_service.runs.attempts import AttemptControl, Lease
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.secrets import require_secrets, secrets_policy
from a13n_service.runs.skills import resolve_skills
from a13n_service.runs.skills import skills_capability as build_skills
from a13n_service.runs.tables import RunRow
from a13n_service.runs.web import ResolvedWeb, open_web, resolve_web
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal, WorkspaceScope, execution_authority
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from pydantic_ai.capabilities import AbstractCapability, Toolset
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio

REVISION = "apr_000000000000000000000000"
ENVIRONMENT = "env_000000000000000000000000"


@dataclass
class Script:
    """The model: each request answers with the next turn, a `(tool, arguments, call_id)` call or final text."""

    turns: list[tuple[str, dict[str, Any], str] | str]
    instructions: list[str] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)

    async def stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        self.instructions.append(str(info.instructions))
        last = messages[-1]
        if isinstance(last, ModelRequest):
            self.results.extend(
                part.content for part in last.parts if isinstance(part, ToolReturnPart | RetryPromptPart)
            )
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            yield turn
        else:
            name, arguments, call_id = turn
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=call_id)}


def mount(root: Path, environment_id: str = ENVIRONMENT) -> EnvironmentMount:
    recipe = DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=root))
    return EnvironmentMount(LocalEnvironment(recipe, environment_id=environment_id, managed=True))


async def run(
    script: Script,
    definition: Sequence[AbstractCapability[AgentContext]],
    *,
    environment: EnvironmentMount | None = None,
    **bindings: Any,
) -> HarnessRunResult[str]:
    """One Harness run of an agent built with the `definition` capabilities, as `runs/execute.py` starts it."""
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=script.stream),
        capabilities=tuple(definition),
    )
    instance = AgentInstanceContext(
        identity=AgentIdentityRef(issuer="a13n-service", subject="usr_x"), agent_instance_id="run_x"
    )
    start = RunBindings(instance=instance, **bindings)
    if environment is None:
        return await executable.run("go", bindings=start)
    return await executable.run(
        "go", environments={"workspace": environment}, default_environment="workspace", bindings=start
    )


def admin(tenant: Any) -> Principal:
    return Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))


def run_row(tenant: Any, *, mounted: bool = True) -> RunRow:
    """The columns of an accepted run that these capabilities read."""
    mounts = [{"name": "workspace", "environment_id": ENVIRONMENT, "working_directory": None}] if mounted else []
    return RunRow(
        id="run_000000000000000000000000000000",
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        principal_id=tenant.principal_id,
        agent_revision_id=REVISION,
        environment_mounts=mounts,
    )


# Web.


class FakeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class SearchBackend:
    queries: list[str] = field(default_factory=list)

    async def __call__(
        self,
        configuration: FakeConfiguration,
        credential: None,
        request: WebSearchRequest,
        options: SearchOptions,
        transport: WebProviderTransport,
    ) -> WebSearchResponse:
        self.queries.append(request.query)
        return WebSearchResponse(results=(WebSearchResult(title="Found", url="https://example.com/found"),))


BACKEND = SearchBackend()
FAKE_SEARCH = WebProviderDefinition(
    type="fake_search", display_name="Fake search", configuration_model=FakeConfiguration, search=BACKEND
)


@dataclass
class WebAdmission:
    """Refuses paid web calls while `refuse` is set and records every call it was asked about."""

    refuse: bool = True
    calls: list[CallContext] = field(default_factory=list)

    async def accept(self, session: Any, intent: Any) -> None:
        pass

    async def proceed(self, session: Any, call: CallContext) -> None:
        self.calls.append(call)
        if self.refuse and call.source == "web.search":
            raise ServiceError("rate_limited", "The web search budget is spent")


def call_check(runtime: Any, tenant: Any) -> CallCheck:
    context = CallContext(
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        session_id="sess_x",
        thread_id="thr_x",
        run_id="run_x",
        run_attempt_id="rat_x",
        root_run_id="run_x",
        call_id="",
        source="",
        provider_id=None,
    )
    return CallCheck(runtime, AttemptControl(), context, models={}, used=0, limit=None)


async def test_a_refused_web_search_never_reaches_its_provider(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    admission = WebAdmission()
    runtime = replace(runtime, registry=Registry.of((*OSS.providers, FAKE_SEARCH)), admission=admission)
    BACKEND.queries.clear()
    actor, scope = admin(tenant), WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    provider = await create_provider(
        runtime.storage,
        actor,
        WebProviderRow,
        tenant.organization_id,
        ProviderCreate(workspace_id=None, type="fake_search", name="Search", config={}),
        registry=runtime.registry,
        keys=runtime.keys,
    )
    tools = WebTools(search=SearchConfiguration(provider_id=provider.id), scrape=None, fetch=None, download=None)
    async with short_session(runtime.storage) as session:
        web = await resolve_web(session, actor, scope, tools, authority=execution_authority(actor, scope))
    assert web is not None

    async def search(call_id: str, check: CallCheck) -> tuple[HarnessRunResult[str], Script]:
        script = Script([("search", {"query": "agents"}, call_id), "done"])
        async with open_web(web, check, runtime=runtime) as binding:
            return await run(script, [WebCapability(web_configuration(tools))], web=binding), script

    # The refusal ends the Harness run; the attempt seals the refusal its check recorded.
    check = call_check(runtime, tenant)
    with pytest.raises(RunError) as refused:
        await search("call_refused", check)
    assert refused.value.code == "call_refused" and BACKEND.queries == []
    assert check.refusal is not None and check.refusal.failure is not None
    assert check.refusal.failure.code == "rate_limited"
    [asked] = admission.calls
    assert (asked.call_id, asked.source, asked.provider_id, asked.tool_name) == (
        "call_refused",
        "web.search",
        provider.id,
        "search",
    )

    admission.refuse = False
    check = call_check(runtime, tenant)
    allowed, script = await search("call_allowed", check)
    assert allowed.output == "done" and check.refusal is None and BACKEND.queries == ["agents"]
    assert admission.calls[-1].call_id == "call_allowed"
    assert script.results[0]["results"][0]["url"] == "https://example.com/found"


def pages() -> FastAPI:
    app = FastAPI()
    app.get("/start")(lambda: RedirectResponse("/page", status_code=302))
    app.get("/page")(lambda: PlainTextResponse("the page"))
    return app


async def test_fetch_follows_redirects_and_refuses_private_addresses(runtime, tenant, listen) -> None:  # type: ignore[no-untyped-def]
    tools = WebTools(search=None, scrape=None, fetch=FetchConfiguration(), download=None)
    web = ResolvedWeb(None, None)

    async def fetch(runtime: Any, url: str) -> Any:
        script = Script([("fetch", {"url": url}, "call_fetch"), "done"])
        async with open_web(web, call_check(runtime, tenant), runtime=runtime) as binding:
            result = await run(script, [WebCapability(web_configuration(tools))], web=binding)
        assert result.output == "done"
        return script.results[0]

    async with listen(pages()) as url:
        # The test deployment allowlists loopback addresses; redirects are followed hop by hop.
        fetched = await fetch(runtime, f"{url}/start")
        assert fetched["ok"] and fetched["content"] == "the page" and fetched["final_url"] == f"{url}/page"

        providers = runtime.settings.providers.model_copy(update={"private_cidrs": ()})
        strict = replace(runtime, settings=runtime.settings.model_copy(update={"providers": providers}))
        literal = await fetch(strict, f"{url}/page")
        assert literal["error"]["code"] == "web_destination_denied"
        # A name is refused where the transport connects, for every address it resolves to.
        named = await fetch(strict, f"{url.replace('127.0.0.1', 'localhost')}/page")
        assert named["error"]["code"] == "web_destination_denied"


# Skills.

SKILL = b"---\nname: code-review\ndescription: Review a change for correctness.\n---\n# Review\n"


def archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for path, data in files.items():
            target.writestr(path, data)
    return output.getvalue()


async def pin_skill(runtime: Any, tenant: Any) -> tuple[Any, str]:
    """A created skill, pinned as a run resolves it, and the object key of its package."""
    actor = admin(tenant)
    upload = await stage(
        runtime.storage,
        runtime.objects,
        actor,
        tenant.workspace_id,
        request_key="skill",
        filename="review.zip",
        content_type="application/zip",
        content=archive({"review/SKILL.md": SKILL, "review/scripts/check.py": b"print('ok')\n"}),
    )
    skill = await create_skill(
        runtime.storage,
        runtime.objects,
        GitHub(runtime.endpoint_policy, timeout=5, max_bytes=1024),
        actor,
        tenant.workspace_id,
        SkillCreate(source=UploadSource(kind="upload", upload_id=upload.upload_id)),
    )
    selection = SkillSelection(skill_id=skill.id, revision_id=skill.default_revision_id)
    async with short_session(runtime.storage) as session:
        [pinned] = await resolve_skills(session, run_row(tenant), [selection])
        with pytest.raises(ServiceError) as unmounted:
            await resolve_skills(session, run_row(tenant, mounted=False), [selection])
    assert unmounted.value.details == {"field": "skills", "reason": "skills need the run's primary environment"}
    return pinned, f"orgs/{tenant.organization_id}/uploads/{upload.upload_id}"


async def test_skills_materialize_once_and_later_attempts_reuse_them(runtime, tenant, tmp_path) -> None:  # type: ignore[no-untyped-def]
    pinned, package = await pin_skill(runtime, tenant)
    proofs: list[str] = []

    async def prove_lease() -> None:
        proofs.append("proved")

    def attempt() -> AbstractCapability[AgentContext]:
        return build_skills([pinned], runtime=runtime, workspace_id=tenant.workspace_id, prove_lease=prove_lease)

    script = Script(["done"])
    assert (await run(script, [attempt()], environment=mount(tmp_path))).output == "done"
    root = tmp_path / ENVIRONMENT / ".a13n" / "skills"
    assert (root / pinned.digest / "SKILL.md").read_bytes() == SKILL
    assert (root / pinned.digest / "scripts" / "check.py").read_bytes() == b"print('ok')\n"
    assert (root / f"{pinned.digest}.complete").read_text() == pinned.digest
    assert f"<path>/workspace/.a13n/skills/{pinned.digest}</path>" in script.instructions[0]
    assert "Review a change for correctness." in script.instructions[0]
    assert proofs == ["proved"]

    # A later attempt finds the completion marker: it writes nothing and never reads the package again.
    await runtime.objects.delete(package)
    script = Script(["done"])
    assert (await run(script, [attempt()], environment=mount(tmp_path))).output == "done"
    assert pinned.digest in script.instructions[0] and proofs == ["proved"]

    # Without the marker the package is read again, and its bytes must still match the revision's digest. The
    # Harness keeps the object store's error as the cause, for the attempt to classify.
    (root / f"{pinned.digest}.complete").unlink()
    (Path(runtime.settings.objects.root) / package).write_bytes(archive({"SKILL.md": SKILL}))
    with pytest.raises(DefinitionError) as failed:
        await run(Script(["done"]), [attempt()], environment=mount(tmp_path))
    cause = failed.value.__cause__
    assert isinstance(cause, ServiceError) and (cause.code, cause.details) == ("unavailable", {"dependency": "objects"})
    assert not (root / f"{pinned.digest}.complete").exists()


# Secrets.

VALUE = "s3cr3t-value-for-tests"
CHILD = "apr_111111111111111111111111"


def secret_tool(name: str, audience: str) -> HarnessTool:
    async def use() -> dict[str, Any]:
        return {"ok": True, "length": len(str(current_invocation_scope().credentials[audience]))}

    return HarnessTool(
        use,
        name=name,
        harness_metadata=HarnessToolMetadata(
            tool_id=f"test.{name}",
            effects=frozenset({"read"}),
            credential_audiences=(audience,),
            idempotency="none",
            output_policy=ToolOutputPolicy(max_inline_bytes=4096, max_output_bytes=4096),
        ),
    )


async def test_a_tool_gets_only_the_secrets_its_node_declares(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    body = SecretCreate.model_validate({"key": "API_KEY", "value": VALUE})
    await create_secret(runtime.storage, runtime.keys, admin(tenant), tenant.workspace_id, body)
    row = run_row(tenant)
    requirements = {REVISION: [SecretRequirement(key="API_KEY")], CHILD: [SecretRequirement(key="OTHER")]}
    with pytest.raises(ServiceError) as missing:
        await require_secrets(runtime, row, requirements)
    assert missing.value.details == {"kind": "secret", "id": "OTHER"}
    await require_secrets(runtime, row, {REVISION: requirements[REVISION]})

    policy = secrets_policy(runtime, row, requirements)
    tools = Toolset(FunctionToolset([secret_tool("use_key", "API_KEY"), secret_tool("use_other", "OTHER")]))
    script = Script([("use_other", {}, "call_other"), ("use_key", {}, "call_key"), "done"])
    result = await run(script, [tools], capabilities=(policy,))
    assert result.output == "done" and result.state is not None
    denied, used = script.results
    assert "denied" in str(denied) and used == {"ok": True, "length": len(VALUE)}
    assert VALUE not in result.state.model_dump_json()

    # An inline child runs under its own definition ID and may use only what that revision declares.
    def node(parent: str | None, definition_id: str | None) -> Any:
        claims = {} if definition_id is None else {"agent_id": definition_id}
        identity = AgentIdentityRef(issuer="a13n-service", subject="usr_x", **claims)
        return SimpleNamespace(instance=SimpleNamespace(parent_agent_instance_id=parent), identity=identity)

    def needing(audience: str) -> HarnessToolMetadata:
        return secret_tool("probe", audience).metadata["a13n.harness.tool"]  # type: ignore[index]

    async def decision(context: Any, audience: str) -> str:
        return (await policy.evaluator(None, needing(audience), context=context)).decision  # type: ignore[arg-type]

    assert await decision(node("agent-parent", CHILD), "OTHER") == "allow"
    assert await decision(node("agent-parent", CHILD), "API_KEY") == "deny"
    assert await decision(node("agent-parent", None), "OTHER") == "deny"
    assert await decision(node(None, None), "API_KEY") == "allow"


# Assets.


async def test_publish_asset_attributes_the_run_and_bounds_the_file(runtime, tenant, tmp_path) -> None:  # type: ignore[no-untyped-def]
    workspace = tmp_path / ENVIRONMENT
    workspace.mkdir()
    (workspace / "report.txt").write_bytes(b"the report")
    (workspace / "large.bin").write_bytes(b"x" * 65537)
    actor = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["builder"]),))
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    lease = Lease(
        run_id="run_000000000000000000000000000000",
        attempt_id="rat_000000000000000000000000000000",
        thread_id="thr_x",
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        number=1,
        worker_id="worker",
        token="token",
    )

    async def publish(runtime: Any, path: str, *, verbs: frozenset[Any] | None = None) -> Any:
        authority = execution_authority(actor, scope)
        if verbs is not None:
            authority = authority.model_copy(update={"verbs": verbs})
        script = Script([("publish_asset", {"path": path}, "call_publish"), "done"])
        result = await run(script, [AssetsCapability(runtime, lease, actor, authority)], environment=mount(tmp_path))
        assert result.output == "done"
        return script.results[0]

    published = await publish(runtime, "/workspace/report.txt")
    asset = await get_asset(runtime.storage, actor, tenant.workspace_id, published["asset_id"])
    assert (asset.name, asset.content_type, asset.size, asset.created_by_id) == (
        "report.txt",
        "text/plain",
        10,
        actor.id,
    )
    assert asset.source == {"run_id": lease.run_id, "run_attempt_id": lease.attempt_id, "tool_call_id": "call_publish"}
    assert (await read_asset_content(runtime.storage, runtime.objects, actor, tenant.workspace_id, asset.id))[1] == (
        b"the report"
    )
    # The same call publishing the same bytes returns the asset it created.
    assert (await publish(runtime, "/workspace/report.txt"))["asset_id"] == asset.id

    objects = runtime.settings.objects.model_copy(update={"max_bytes": 65536})
    bounded = replace(runtime, settings=runtime.settings.model_copy(update={"objects": objects}))
    assert "exceeds the 65536-byte asset limit" in str(await publish(bounded, "/workspace/large.bin"))
    assert "does not cover" in str(await publish(runtime, "/workspace/report.txt", verbs=frozenset({"read", "run"})))
    assert "regular file" in str(await publish(runtime, "/workspace"))
